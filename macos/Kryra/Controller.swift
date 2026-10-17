import Foundation
import Combine

@MainActor final class KeyboardController: ObservableObject {
    @Published private(set) var connected = false
    @Published private(set) var devices: [DeviceDescriptor] = []
    @Published private(set) var device: DeviceDescriptor?
    @Published private(set) var firmware: FirmwareState?
    @Published private(set) var updatedAt: Date?
    @Published private(set) var issue: String?
    @Published private(set) var busy = false
    @Published private(set) var command: LightingCommand?
    @Published private(set) var framesSent = 0
    @Published private(set) var selection: String
    @Published private(set) var sleeping = false
    let demo: Bool
    private let worker: HIDWorker
    private let defaults: UserDefaults
    private var pollTask: Task<Void,Never>?, lightingTask: Task<Void,Never>?
    private var generation = 0, failures = 0
    private var ownsControl = false, closing = false
    private var operations = 0, revision = 0
    private var startedAt: TimeInterval = 0, firmwareReceivedAt: TimeInterval = 0
    private var activeRequestID: UInt32?
    init(worker: HIDWorker = HIDWorker(), demo: Bool = false, defaults: UserDefaults = .standard) {
        self.worker = worker; self.demo = demo; self.defaults = defaults
        selection = defaults.string(forKey:"selectedDevice") ?? "auto"
    }
    var battery: Int? { firmware?.percent }
    var stale: Bool { !connected || updatedAt.map { Date().timeIntervalSince($0) > 65 } ?? true }
    var effectElapsed: TimeInterval { max(0,ProcessInfo.processInfo.systemUptime-startedAt) }
    var mode: String { command?.mode ?? (connected && firmware?.active == true && firmwareRemainingMS > 0 ? "external" : "original") }
    private var firmwareRemainingMS: Double { max(0,Double(firmware?.remainingMS ?? 0)-(ProcessInfo.processInfo.systemUptime-firmwareReceivedAt)*1000) }
    var connectionName: String { device?.transport == "usb" ? "USB" : device?.transport == "rf" ? "2.4G" : "未连接" }
    var modeName: String {
        switch mode { case "solid":return "纯色"; case "frame":return "逐段颜色"; case "blink":return "闪烁提醒"; case "breathe":return "呼吸提醒"; case "rainbow":return "彩虹播放中"
        case "external":return "其他程序正在控灯"; default:return "键盘原灯效" }
    }
    func start() {
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self, !closing else { return }
                if !sleeping && !ownsControl { _ = try? await refresh() }
                let seconds = devices.isEmpty ? 5.0 : min(120.0,30.0 * pow(2,Double(max(0,failures-1))))
                do { try await Task.sleep(nanoseconds:UInt64(seconds*1e9)) } catch { return }
            }
        }
    }
    @discardableResult func refresh() async throws -> [String:Any] {
        try Task.checkCancellation()
        guard !closing, !sleeping else { throw ControlError("电脑正在休眠。") }
        let requestedRevision = revision
        operations += 1; busy = true
        defer { operations -= 1; busy = operations > 0 }
        do {
            let target = ownsControl ? device?.id ?? selection : selection
            let reading = try await worker.perform { try $0.connect(selection:target) }
            try Task.checkCancellation()
            guard requestedRevision == revision, !sleeping, !closing else { throw ControlError("设备状态已改变，请重新刷新。",status:409) }
            apply(reading); failures = 0; issue = nil
            return snapshot()
        } catch {
            if requestedRevision == revision, !sleeping, !closing, !(error is CancellationError) {
                connected = false; failures += 1; issue = error.localizedDescription
                command = nil; ownsControl = false; lightingTask?.cancel(); lightingTask = nil
                if let scanned = try? await worker.perform({ try $0.scan() }) { devices = scanned }
            }
            throw error
        }
    }
    func select(_ id: String) async {
        if ownsControl { _ = try? await release() }
        generation += 1; revision += 1
        selection = id; defaults.set(id,forKey:"selectedDevice")
        _ = try? await refresh()
    }
    func setLighting(_ request: LightingCommand) async throws -> [String:Any] {
        try Task.checkCancellation()
        guard !closing, !sleeping else { throw ControlError("电脑正在休眠或应用正在退出。") }
        if request.mode == "release" { return try await release() }
        generation += 1; let epoch = generation
        lightingTask?.cancel(); lightingTask = nil
        operations += 1; busy = true; defer { operations -= 1; busy = operations > 0 }
        do {
            // A fresh INFO guards every new operation, including firmware or
            // connection-mode changes since the previous battery poll.
            let target = connected ? device?.id ?? selection : selection
            let reading = try await worker.perform { try $0.connect(selection:target) }
            try Task.checkCancellation()
            guard generation == epoch, !closing, !sleeping else { throw ControlError("操作已由新的请求替代。",status:409) }
            apply(reading)
            guard reading.state.permits(reading.device.transport) else { throw ControlError("此固件尚未开放当前通道的灯光控制。") }
            let native = request.nativeEffectID != nil
            if native && !reading.state.nativeEffects {
                throw ControlError("本地灯效需要 v3.1.0 或更新的兼容固件，请先升级固件。")
            }
            let first: DeviceReading
            var requestID: UInt32?
            if native {
                let id = request.requestID ?? UInt32.random(in:1...UInt32.max)
                let payload = try request.nativePayload(requestID:id)
                first = try await worker.perform { try $0.command(.play,payload:payload) }
                requestID = id
            } else {
                // Nine independent static colors retain the legacy wire format.
                // Its bounded watchdog finishes without any host heartbeat.
                let timeoutMS = Int(ceil(request.duration!*1000))
                _ = try await worker.perform { try $0.command(.timeout,payload:[UInt8(timeoutMS & 255),UInt8(timeoutMS >> 8)]) }
                try Task.checkCancellation()
                guard generation == epoch, !closing, !sleeping else { throw ControlError("操作已由新的请求替代。",status:409) }
                first = try await worker.perform { try $0.command(.frame,payload:request.payload(elapsed:0)) }
            }
            try Task.checkCancellation()
            guard generation == epoch, !closing, !sleeping else { throw ControlError("操作已由新的请求替代。",status:409) }
            apply(first); ownsControl = first.state.active; command = first.state.active ? request : nil
            activeRequestID = requestID
            framesSent = 1; issue = nil
            startedAt = ProcessInfo.processInfo.systemUptime - max(0,ceil(request.duration!*1000)-Double(first.state.remainingMS))/1000
            // UI expiry only. Firmware owns the deadline; no renewal, RGB
            // stream, polling or delayed RELEASE is needed to finish the effect.
            // In particular an old timer cannot cancel a newer external command.
            let remaining = Double(first.state.remainingMS)/1000
            lightingTask = Task { [weak self] in
                do { try await Task.sleep(nanoseconds:UInt64(max(0.001,remaining)*1e9)) }
                catch { return }
                guard let self, generation == epoch, !closing, !sleeping else { return }
                command = nil; ownsControl = false; lightingTask = nil
            }
            return snapshot()
        } catch {
            if generation == epoch { command = nil; ownsControl = false; if !(error is CancellationError) { issue = error.localizedDescription } }
            throw error
        }
    }
    @discardableResult func release() async throws -> [String:Any] {
        generation += 1; lightingTask?.cancel(); lightingTask = nil
        let shouldSend = connected || ownsControl
        command = nil; ownsControl = false
        guard shouldSend else { return snapshot() }
        do { apply(try await worker.perform(ignoreCancellation:true) { try $0.command(.release) }); issue = nil }
        catch { issue = "发送已停止，等待键盘超时恢复。\(error.localizedDescription)"; throw error }
        return snapshot()
    }
    func suspend() async {
        sleeping = true; revision += 1
        if ownsControl { _ = try? await release() }
        connected = false
    }
    func resume() async { sleeping = false; _ = try? await refresh() }
    func shutdown() async {
        closing = true; revision += 1; pollTask?.cancel()
        if ownsControl { _ = try? await release() }
        lightingTask?.cancel(); worker.stop()
    }
    private func apply(_ reading: DeviceReading) {
        device = reading.device; firmware = reading.state; devices = reading.devices
        updatedAt = Date(); connected = true; firmwareReceivedAt = ProcessInfo.processInfo.systemUptime
        if ownsControl && !reading.state.active { command = nil; ownsControl = false; lightingTask?.cancel(); lightingTask = nil }
    }
    func batteryJSON() -> [String:Any] {
        ["percent":battery as Any? ?? NSNull(),"connected":connected,"stale":stale,
         "updated_at":updatedAt.map { ISO8601DateFormatter().string(from:$0) } as Any? ?? NSNull(),
         "transport":device?.transport as Any? ?? NSNull(),"demo":demo]
    }
    func snapshot() -> [String:Any] {
        var descriptor = device?.json ?? [:]
        descriptor["connected"] = connected
        descriptor["firmware_version"] = firmware?.version as Any? ?? NSNull()
        descriptor["firmware_build"] = firmware?.build as Any? ?? NSNull()
        descriptor["native_effects"] = firmware?.nativeEffects ?? false
        let lighting: [String:Any] = ["mode":mode,"active":ownsControl,"brightness":command?.brightness ?? 100,
            "colors":command?.colors.map(\.hex) ?? [],"period_ms":command?.periodMS ?? 5000,
            "duration_seconds":command?.duration as Any? ?? NSNull(),"continuous":false,
            "request_id":ownsControl ? activeRequestID as Any? ?? NSNull() : NSNull(),"remaining_ms":Int(ceil(firmwareRemainingMS)),
            "execution":command?.nativeEffectID != nil ? "firmware" : "static_frame", "commands_sent":framesSent,
            "prevents_idle_sleep":ownsControl && command?.nativeEffectID == nil && device?.transport == "rf","frames_sent":framesSent]
        let result: [String:Any] = ["app_version":AppIdentity.version,"api_version":1,"demo":demo,"device":descriptor,
            "devices":devices.map(\.json),"battery":batteryJSON(),"sleeping":sleeping,
            "lighting":lighting,"error":issue as Any? ?? NSNull()]
        return result
    }
    func handle(_ request: HTTPRequest) async -> HTTPResponse {
        do {
            switch (request.method,request.path) {
            case ("GET","/v1/health"): return HTTPResponse(["ok":true,"app_version":AppIdentity.version,"api_version":1,"demo":demo])
            case ("GET","/v1/status"): return HTTPResponse(snapshot())
            case ("GET","/v1/battery"):
                if request.query.contains(where:{$0.name == "refresh" && ["true","1"].contains($0.value ?? "")}) { _ = try await refresh() }
                return HTTPResponse(batteryJSON())
            case ("POST","/v1/refresh"): return HTTPResponse(try await refresh())
            case ("POST","/v1/lighting"): return HTTPResponse(try await setLighting(LightingCommand.parse(request.json())))
            case ("POST","/v1/lighting/release"):
                if !request.body.isEmpty, try !request.json().isEmpty { throw ControlError("释放命令不接受额外参数。",status:400) }
                return HTTPResponse(try await release())
            default: return .error(ControlError("接口不存在。",status:404))
            }
        } catch { return .error(error) }
    }
}

/// Explicit demo mode for UI and API testing; never creates an IOKit backend.
final class DemoBackend: HIDBackend {
    private let device = DeviceDescriptor(id:"demo-usb",name:"Q101 演示键盘",transport:"usb")
    private var active = false, last = 0.0, sequence: UInt8 = 0, timeout = 2000
    private var legacyTimeout = 2000, native = false, lastID: [UInt8] = [], lastPayload: [UInt8] = []
    func scan() throws -> [DeviceDescriptor] { [device] }
    func exchange(_ request: [UInt8], deviceID: String) throws -> [UInt8] {
        let now = ProcessInfo.processInfo.systemUptime
        if (now-last)*1000 >= Double(timeout) { active = false }
        let op = request[3] & 7
        var status: UInt8 = 0
        if op == 1 { native = false; active = true; last = now; sequence = request[4]; timeout = legacyTimeout }
        if op == 2 { active = false }
        if op == 3 && active && !native { last = now }
        if op == 4 { legacyTimeout = Int(request[5]) | Int(request[6]) << 8; if !native { timeout = legacyTimeout } }
        if op == 5 {
            let id = Array(request[14..<18])
            if id != lastID {
                lastID = id; lastPayload = Array(request[5..<18]); native = true; active = true; last = now; sequence = request[4]
                timeout = Int(request[12]) | Int(request[13]) << 8
            } else if Array(request[5..<18]) != lastPayload { status = 3 }
        }
        var reply = Array(request.prefix(5)) + Array(repeating:UInt8(0),count:27)
        reply[3] |= 8; reply[6] = 1; reply[7] = 9; reply[8] = active ? 1 : 0
        reply[5] = status
        if op == 3 && (!active || native) { reply[5] = 4 }
        reply[9] = 88; reply[10] = 2; reply[11] = sequence; reply[12] = 3
        reply[13] = UInt8(timeout & 255); reply[14] = UInt8(timeout >> 8)
        let remaining = active ? max(0,timeout-Int((now-last)*1000)) : 0
        reply[15] = UInt8(remaining & 255); reply[16] = UInt8(remaining >> 8)
        reply.replaceSubrange(17..<21,with:Array("Q101".utf8)); reply[21] = 162; reply[22] = 31
        reply.replaceSubrange(23..<32,with:[0x46,0x56,1,3,1,0,31,3,0])
        return reply
    }
    func close() {}
}
