import Foundation
import IOKit.hid

struct DeviceDescriptor: Identifiable, Equatable {
    let id: String, name: String, transport: String
    var label: String { "\(name) · \(transport == "usb" ? "USB" : "2.4G")" }
    var json: [String: Any] { ["id":id,"name":name,"transport":transport] }
}
struct DeviceReading {
    let device: DeviceDescriptor, state: FirmwareState, devices: [DeviceDescriptor]
}

protocol HIDBackend: AnyObject {
    func scan() throws -> [DeviceDescriptor]
    func exchange(_ packet: [UInt8], deviceID: String) throws -> [UInt8]
    func close()
}

private final class ReportInbox {
    let device: IOHIDDevice
    let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: 64)
    var received: [[UInt8]] = []
    init(_ device: IOHIDDevice) {
        self.device = device
        IOHIDDeviceRegisterInputReportCallback(device, buffer, 64, { context, result, _, _, _, report, count in
            guard let context, result == kIOReturnSuccess, count > 0, count <= 64 else { return }
            let inbox = Unmanaged<ReportInbox>.fromOpaque(context).takeUnretainedValue()
            if inbox.received.count < 64 { inbox.received.append(Array(UnsafeBufferPointer(start: report, count: count))) }
        }, Unmanaged.passUnretained(self).toOpaque())
        IOHIDDeviceScheduleWithRunLoop(device, CFRunLoopGetCurrent(), CFRunLoopMode.defaultMode.rawValue)
    }
    func close() {
        IOHIDDeviceUnscheduleFromRunLoop(device, CFRunLoopGetCurrent(), CFRunLoopMode.defaultMode.rawValue)
        IOHIDDeviceRegisterInputReportCallback(device, buffer, 64, nil, nil)
        IOHIDDeviceClose(device, IOOptionBits(kIOHIDOptionsTypeNone))
    }
    deinit { buffer.deallocate() }
}

final class IOKitBackend: HIDBackend {
    private let manager = IOHIDManagerCreate(kCFAllocatorDefault, IOOptionBits(kIOHIDOptionsTypeNone))
    private var managerResult: IOReturn = kIOReturnSuccess
    private var devices: [String: IOHIDDevice] = [:]
    private var channels: [String: ReportInbox] = [:]
    init() {
        let matches: [[String: Any]] = [0x5055,0x5088].map { pid in
            [kIOHIDVendorIDKey:0x320f, kIOHIDProductIDKey:pid,
             kIOHIDPrimaryUsagePageKey:0xff60, kIOHIDPrimaryUsageKey:0x61]
        }
        IOHIDManagerSetDeviceMatchingMultiple(manager, matches as CFArray)
        IOHIDManagerScheduleWithRunLoop(manager, CFRunLoopGetCurrent(), CFRunLoopMode.defaultMode.rawValue)
        managerResult = IOHIDManagerOpen(manager, IOOptionBits(kIOHIDOptionsTypeNone))
    }
    func scan() throws -> [DeviceDescriptor] {
        try check(managerResult, "打开 HID 管理器")
        CFRunLoopRunInMode(.defaultMode, 0.003, true)
        let set = IOHIDManagerCopyDevices(manager) as? Set<IOHIDDevice> ?? []
        var found: [String: IOHIDDevice] = [:], result: [DeviceDescriptor] = []
        for device in set {
            var registry: UInt64 = 0
            guard IORegistryEntryGetRegistryEntryID(IOHIDDeviceGetService(device), &registry) == KERN_SUCCESS else { continue }
            let pid = (IOHIDDeviceGetProperty(device, kIOHIDProductIDKey as CFString) as? NSNumber)?.intValue
            let transport = pid == 0x5088 ? "rf" : "usb"
            let location = (IOHIDDeviceGetProperty(device,kIOHIDLocationIDKey as CFString) as? NSNumber)?.uint64Value ?? 0
            // Location survives unplug/replug at the same port; registry IDs
            // identify only this enumeration. Keep the latter as a fallback.
            let id = transport + "-" + String(location == 0 ? registry : location,radix:16)
            let name = IOHIDDeviceGetProperty(device, kIOHIDProductKey as CFString) as? String ?? "Q101 / MQ80"
            found[id] = device
            result.append(DeviceDescriptor(id:id, name:name, transport:transport))
        }
        for id in Array(channels.keys) {
            if found[id] == nil || !CFEqual(channels[id]!.device,found[id]!) { channels.removeValue(forKey:id)?.close() }
        }
        devices = found
        return result.sorted { ($0.transport == "usb" ? "0" : "1") + $0.id < ($1.transport == "usb" ? "0" : "1") + $1.id }
    }
    func exchange(_ packet: [UInt8], deviceID: String) throws -> [UInt8] {
        guard packet.count == 32, let device = devices[deviceID] else { throw ControlError("键盘已断开。") }
        let inbox: ReportInbox
        if let existing = channels[deviceID] { inbox = existing }
        else {
            try check(IOHIDDeviceOpen(device, IOOptionBits(kIOHIDOptionsTypeNone)), "打开 Raw HID 接口")
            inbox = ReportInbox(device); channels[deviceID] = inbox
        }
        CFRunLoopRunInMode(.defaultMode, 0.001, true)
        inbox.received.removeAll(keepingCapacity:true)
        let result = packet.withUnsafeBufferPointer { IOHIDDeviceSetReport(device, kIOHIDReportTypeOutput, 0, $0.baseAddress!, 32) }
        try check(result, "发送键盘请求")
        let deadline = ProcessInfo.processInfo.systemUptime + 1
        while ProcessInfo.processInfo.systemUptime < deadline {
            while !inbox.received.isEmpty {
                let reply = inbox.received.removeFirst()
                if Wire.matches(packet, reply) { return reply }
            }
            CFRunLoopRunInMode(.defaultMode, 0.01, true)
        }
        throw ControlError("键盘未响应；如果正在休眠，请先按键唤醒。")
    }
    func close() {
        channels.values.forEach { $0.close() }; channels.removeAll(); devices.removeAll()
        IOHIDManagerUnscheduleFromRunLoop(manager, CFRunLoopGetCurrent(), CFRunLoopMode.defaultMode.rawValue)
        IOHIDManagerClose(manager, IOOptionBits(kIOHIDOptionsTypeNone))
    }
    private func check(_ code: IOReturn, _ action: String) throws {
        guard code == kIOReturnSuccess else { throw ControlError(String(format:"%@失败（0x%08X），请关闭占用接口的 VIA 窗口后重试。",action,code)) }
    }
}

final class DeviceGateway {
    let backend: HIDBackend
    private var sequence: UInt8 = 0
    private var current: DeviceDescriptor?
    private var verified: FirmwareState?
    private var devices: [DeviceDescriptor] = []
    init(_ backend: HIDBackend) { self.backend = backend }
    func connect(selection: String) throws -> DeviceReading {
        devices = try backend.scan()
        let candidates = selection == "auto" ? devices : devices.filter { $0.id == selection }
        guard !candidates.isEmpty else { current = nil; verified = nil; throw ControlError("未检测到 Q101 / MQ80，请连接 USB 数据线或 2.4G 接收器。") }
        if selection == "auto", Dictionary(grouping:candidates, by: \.transport).values.contains(where:{$0.count > 1}) {
            throw ControlError("发现多个键盘或接收器，请在设备菜单中选择一个。")
        }
        var last: Error = ControlError("键盘未响应。")
        for candidate in candidates {
            do {
                let state = try request(.info, device:candidate)
                guard state.connection == candidate.transport else { throw ControlError("请将键盘切到对应的 USB 或 2.4G 模式。") }
                current = candidate; verified = state
                return DeviceReading(device:candidate,state:state,devices:devices)
            } catch { last = error }
        }
        current = nil; verified = nil; throw last
    }
    func scan() throws -> [DeviceDescriptor] { devices = try backend.scan(); return devices }
    func command(_ command: Wire.Command, payload: [UInt8] = []) throws -> DeviceReading {
        guard let current, let verified, verified.permits(current.transport) else { throw ControlError("请先连接支持该通道的键盘。") }
        guard command != .play || verified.nativeEffects else { throw ControlError("本地灯效需要 v3.1.0 或更新的兼容固件，请先升级固件。") }
        do {
            let state = try request(command, payload:payload, device:current)
            guard state.connection == current.transport else { throw ControlError("键盘连接模式已改变，请重新连接。") }
            self.verified = state
            return DeviceReading(device:current,state:state,devices:devices)
        } catch { self.verified = nil; throw error }
    }
    private func request(_ command: Wire.Command, payload: [UInt8] = [], device: DeviceDescriptor) throws -> FirmwareState {
        sequence &+= 1
        let request = try Wire.packet(command,sequence:sequence,payload:payload)
        return try Wire.decode(request,backend.exchange(request,deviceID:device.id))
    }
}

/// Every IOKit call and CFRunLoop callback stays on one dedicated thread.
private final class WorkCancellation: @unchecked Sendable {
    private let lock = NSLock()
    private var value = false
    func cancel() { lock.lock(); value = true; lock.unlock() }
    var cancelled: Bool { lock.lock(); defer { lock.unlock() }; return value }
}

final class HIDWorker {
    private let condition = NSCondition()
    private var jobs: [(DeviceGateway) -> Void] = []
    private var stopping = false
    private var thread: Thread!
    init(factory: @escaping () -> HIDBackend = { IOKitBackend() }) {
        thread = Thread { [self] in
            let gateway = DeviceGateway(factory())
            while true {
                condition.lock()
                while jobs.isEmpty && !stopping { condition.wait() }
                if stopping && jobs.isEmpty { condition.unlock(); break }
                let job = jobs.removeFirst(); condition.unlock()
                autoreleasepool { job(gateway) }
            }
            gateway.backend.close()
        }
        thread.name = "Kryra Raw HID"; thread.qualityOfService = .utility; thread.start()
    }
    func perform<T>(ignoreCancellation: Bool = false, _ operation: @escaping (DeviceGateway) throws -> T) async throws -> T {
        let cancellation = WorkCancellation()
        return try await withTaskCancellationHandler {
            if !ignoreCancellation { try Task.checkCancellation() }
            return try await withCheckedThrowingContinuation { continuation in
                condition.lock()
                guard !stopping else { condition.unlock(); continuation.resume(throwing:ControlError("应用正在退出。")); return }
                jobs.append { gateway in
                    guard !cancellation.cancelled else { continuation.resume(throwing:CancellationError()); return }
                    do { continuation.resume(returning:try operation(gateway)) }
                    catch { continuation.resume(throwing:error) }
                }
                condition.signal(); condition.unlock()
            }
        } onCancel: {
            if !ignoreCancellation { cancellation.cancel() }
        }
    }
    func stop() { condition.lock(); stopping = true; condition.signal(); condition.unlock() }
}
