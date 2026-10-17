import AppKit
import Foundation
import SwiftUI
import UniformTypeIdentifiers

struct LabError: LocalizedError {
    let message: String
    var state: JSONValue = .null
    var errorDescription: String? { message }
}

@MainActor final class EngineConnection {
    private let resourceDirectory: URL?
    private let dataDirectory: URL?
    init(resources:URL? = nil,dataDirectory:URL? = nil) { self.resourceDirectory = resources; self.dataDirectory = dataDirectory }
    private var process: Process?
    private var input: FileHandle?
    private var output: FileHandle?
    private var buffer = Data()
    private var nextID = 0
    private var stopping = false
    private var pending: [Int: (CheckedContinuation<JSONValue, Error>, DispatchWorkItem)] = [:]

    func launch() throws {
        let support = dataDirectory ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("MQ80 Firmware Lab", isDirectory: true)
        try FileManager.default.createDirectory(at: support, withIntermediateDirectories: true)
        guard let resources = resourceDirectory ?? Bundle.main.resourceURL else { throw LabError(message: "找不到应用资源目录。") }
        let executable = resources.appendingPathComponent("engine/MQ80LabEngine")
        let task = Process(); task.executableURL = executable
        task.arguments = ["--data-dir", support.path, "--parent-pid", String(ProcessInfo.processInfo.processIdentifier)]
        let incoming = Pipe(), outgoing = Pipe()
        task.standardInput = incoming; task.standardOutput = outgoing
        let log = support.appendingPathComponent("native-runtime.log")
        FileManager.default.createFile(atPath: log.path, contents: nil)
        task.standardError = try FileHandle(forWritingTo: log)
        input = incoming.fileHandleForWriting; output = outgoing.fileHandleForReading
        outgoing.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty { handle.readabilityHandler = nil }
            DispatchQueue.main.async { self?.receive(data) }
        }
        task.terminationHandler = { [weak self] task in
            DispatchQueue.main.async {
                guard let self, !self.stopping else { return }
                self.failPending(LabError(message: "固件引擎已退出（\(task.terminationStatus)）。诊断日志：~/Library/Application Support/MQ80 Firmware Lab/native-runtime.log"))
            }
        }
        process = task; try task.run()
    }

    func call(_ method: String, _ params: JSONValue = .object([:])) async throws -> JSONValue {
        guard let input, process?.isRunning == true else { throw LabError(message: "固件引擎尚未连接。") }
        nextID += 1; let id = nextID
        let message: JSONValue = ["id": .int(id), "method": .string(method), "params": params]
        var bytes = try message.data(); bytes.append(10)
        return try await withCheckedThrowingContinuation { continuation in
            let timeout = DispatchWorkItem { [weak self] in
                guard let entry = self?.pending.removeValue(forKey: id) else { return }
                entry.0.resume(throwing: LabError(message: "固件引擎响应超时，请检查诊断日志。"))
            }
            pending[id] = (continuation, timeout)
            DispatchQueue.main.asyncAfter(deadline: .now() + 30, execute: timeout)
            do { try input.write(contentsOf: bytes) }
            catch { pending.removeValue(forKey: id)?.1.cancel(); continuation.resume(throwing: error) }
        }
    }

    private func receive(_ data: Data) {
        if data.isEmpty { if !stopping { failPending(LabError(message: "固件引擎连接已关闭。")) }; return }
        buffer.append(data)
        if buffer.count > 32 * 1024 * 1024 { failPending(LabError(message: "引擎响应超过限制。")); stop(); return }
        while let line = buffer.range(of: Data([10])) {
            let value = buffer.subdata(in: 0..<line.lowerBound); buffer.removeSubrange(0..<line.upperBound)
            do {
                let response = try JSONDecoder().decode(JSONValue.self, from: value)
                guard let entry = pending.removeValue(forKey: response["id"].int) else { continue }
                entry.1.cancel()
                if response["ok"].flag { entry.0.resume(returning: response["result"]) }
                else { entry.0.resume(throwing: LabError(message: response["error"]["message"].text, state: response["error"]["state"])) }
            } catch { failPending(LabError(message: "无法解析引擎响应：\(error.localizedDescription)")) }
        }
    }

    private func failPending(_ error: Error) {
        let requests = pending; pending.removeAll()
        for (_, item) in requests { item.1.cancel(); item.0.resume(throwing: error) }
    }
    func stop() {
        stopping = true; output?.readabilityHandler = nil
        try? input?.close(); if process?.isRunning == true { process?.terminate() }
        failPending(LabError(message: "应用已退出。"))
    }
}

@MainActor final class LabModel: ObservableObject {
    @Published var ready = false
    @Published var page: LabPage = .workbench {
        didSet { if page != .workbench && keyboardCaptureActive { setKeyboardCapture(false) } }
    }
    @Published var bootstrap: JSONValue = .null
    @Published var runtime: JSONValue = .null
    @Published var keys: [KeySpec] = []
    @Published var environment: [String: JSONValue] = [:]
    @Published var selectedFirmware = ""
    @Published var comparisonFirmware = ""
    @Published var playing = false
    @Published var clockMode = "preview"
    @Published var speed = 0.0
    @Published var pendingCount = 0
    @Published var status = "正在启动固件引擎…"
    @Published var issue: String?
    @Published var combo = false {
        didSet { if oldValue && !combo { releaseKeys() } }
    }
    @Published var selectedKeys: Set<String> = []
    @Published private(set) var physicalKeys: Set<String> = []
    @Published private(set) var virtualFnHeld = false
    @Published private(set) var keyboardCaptureActive = false
    @Published var rightCommandIsFn = true {
        didSet { if oldValue != rightCommandIsFn { releaseKeys() } }
    }
    @Published var showKeyLighting = true
    @Published var showCoordinates = false
    @Published var showInspector = true
    @Published var selectedScenario = ""
    @Published var scenarioJSON = ""
    @Published var job: JSONValue = .null
    @Published var memoryAddress = "0x80b80"
    @Published var memorySize = 64
    @Published var memoryText = "选择地址读取虚拟内存。"
    @Published var otaCase = "valid_image"
    @Published var sideHIDColors = Array(repeating:[255,128,0],count:9)
    @Published var sideHIDBrightness = 100.0
    @Published var sideHIDPeriod = 5.0

    private let engine: EngineConnection
    init(engineResources:URL? = nil,dataDirectory:URL? = nil) { engine = EngineConnection(resources:engineResources,dataDirectory:dataDirectory) }
    private var started = false
    private var transportBusy = false
    private var actionTail: Task<JSONValue?, Never>?
    private var intentTail: Task<Void,Never>?
    private var clockTask: Task<Void, Never>?
    private var epoch = 0
    private var clockAt = 0.0
    private var envPending: [String: JSONValue] = [:]
    private var debounce: [String: Task<Void, Never>] = [:]
    private var physicalInput = PhysicalKeyboardState()
    private var sideEditTask: Task<Void,Never>?

    var sideHID: JSONValue { runtime["side_hid"] }
    var sideHIDReady: Bool { sideHID["supported"].flag && sideHID["control_ready"].flag }
    var sideHIDConnection: String { environment["connection"]?.text == "rf" ? "rf" : "usb" }
    var sideHIDTransportName: String { sideHIDConnection == "rf" ? "2.4G" : "USB" }
    var firmwareVersionCaption: String {
        let version = runtime["firmware"]["firmware_version"]
        guard !version.isNull else { return "此镜像未提供可查询的固件版本号" }
        let labels = ["fn_b_battery_bar":"电量条", "battery_query":"电量查询", "usb_side_rgb":"USB 控灯", "rf_side_rgb":"2.4G 控灯"]
        let features = runtime["firmware"]["firmware_features"].values.map{labels[$0.text] ?? $0.text}.joined(separator:" / ")
        return "固件 v\(version.text) · 构建 \(runtime["firmware"]["firmware_build"].int) · \(features)"
    }
    var sideHIDCaption: String {
        if !sideHID["supported"].flag { return "请载入侧灯实验固件" }
        if sideHIDConnection == "rf" && !sideHID["rf_supported"].flag { return "2.4G 需要 USB + 2.4G 实验固件" }
        if runtime["status"].text == "sleeping" { return "键盘已休眠 · 请先按键唤醒" }
        if !sideHID["control_ready"].flag { return "等待虚拟 \(sideHIDTransportName) 主机" }
        if runtime["battery"]["query_active"].flag { return "电量条优先 · 结束后恢复侧灯" }
        if sideHID["mode"].text == "rainbow" { return playing ? "\(sideHIDTransportName) 彩虹播放中 · \(sideHID["fps"].int) FPS" : "彩虹已暂停" }
        if sideHID["mode"].text != "off" { return "电脑颜色控制中" }
        return sideHID["active"].flag ? "已停止发送 · 等待超时恢复" : "原侧灯状态"
    }

    var highlightedKeys: Set<String> { selectedKeys.union(physicalKeys).union(runtime["keys"].values.map(\.text)) }

    var mainPixels: [Pixel] { runtime["rgb"]["main"].values.map(Pixel.init) }
    var sidePixels: [Pixel] { runtime["rgb"]["side"].values.map(Pixel.init) }
    var trace: [TraceRow] { runtime["trace"].values.reversed().map { TraceRow(value: $0) } }
    var images: [JSONValue] { bootstrap["firmwares"].values }
    var scenarios: [JSONValue] { bootstrap["scenarios"].values }
    var jobRunning: Bool { job["status"].text == "running" }
    var runtimeLabel: String {
        switch runtime["status"].text { case "ready": return "运行就绪"; case "sleeping": return "等待唤醒"; case "fault": return "执行已停止"; case "restoring": return "恢复扫描中"; default: return "连接中" }
    }
    var queryCaption: String {
        let battery = runtime["battery"]
        if !battery["query_active"].flag { return "普通灯效" }
        if battery["query_remaining_ms"].isNull { return "正在显示电量" }
        return String(format: "电量条 %.1f s", battery["query_remaining_ms"].double/1000)
    }

    func start() {
        guard !started else { return }; started = true
        AppDelegate.shutdown = { [weak self] in self?.engine.stop() }
        Task {
            do {
                try engine.launch()
                try await loadBootstrap()
                apply(try await engine.call("state"), foreground: true)
                ready = true; status = "原生界面 · 离线固件引擎"
            } catch { show(error) }
        }
    }

    private func loadBootstrap() async throws {
        bootstrap = try await engine.call("bootstrap")
        keys = try JSONDecoder().decode([KeySpec].self, from: bootstrap["layout"].data())
        if comparisonFirmware.isEmpty { comparisonFirmware = images.first(where: { $0["id"].text != selectedFirmware })?["id"].text ?? "" }
        if selectedScenario.isEmpty, let first = scenarios.first { selectScenario(first["id"].text) }
    }

    private func apply(_ value: JSONValue, foreground: Bool) {
        runtime = value
        if foreground {
            selectedFirmware = value["firmware"]["id"].text
            environment = value["environment"].objectValue.merging(envPending) { _, new in new }
        }
        if !value["error"].isNull {
            issue = "\(value["error"]["task"].text) @ \(value["error"]["pc"].text)：\(value["error"]["message"].text)"
            pause()
        }
    }
    private func show(_ error: Error) {
        if let e = error as? LabError, !e.state.isNull { apply(e.state, foreground: true) }
        issue = error.localizedDescription; status = "操作未完成"; pause()
    }
    private func success(_ message: String) { issue = nil; status = message }

    private func finishEnvironment() async {
        let edits = Array(debounce.values)
        for edit in edits { await edit.value }
    }
    private func finishInput() async {
        if let edits = sideEditTask { await edits.value }
        if let previous = intentTail { await previous.value }
        await finishEnvironment()
        if let previous = actionTail { _ = await previous.value }
    }
    private func submit(_ operation: @escaping @MainActor () async -> Void) {
        let previous = intentTail
        let task = Task { if let previous { await previous.value }; await operation() }
        intentTail = task
    }
    func capture(_ method:String,_ params:JSONValue = .object([:])) async throws -> JSONValue {
        await finishInput()
        return try await engine.call(method,params)
    }

    func perform(_ params: JSONValue, background: Bool = false, generation: Int = -1) async -> JSONValue? {
        if !background { pendingCount += 1 }
        let previous = actionTail
        let task = Task<JSONValue?, Never> { [weak self] in
            if let previous { _ = await previous.value }
            guard let self else { return nil }
            if background && (!playing || generation != epoch || pendingCount > 0) { return nil }
            transportBusy = true
            if background { clockAt = ProcessInfo.processInfo.systemUptime }
            defer {
                transportBusy = false
                if !background { pendingCount -= 1; clockAt = ProcessInfo.processInfo.systemUptime }
            }
            do {
                let result = try await engine.call("action", params)
                apply(result, foreground: !background)
                if ["reset","restore","load_job"].contains(params["action"].text) { syncSideHIDControls() }
                return result["error"].isNull ? result : nil
            } catch { show(error); return nil }
        }
        actionTail = task
        return await task.value
    }

    func command(_ params: JSONValue, pauseFirst: Bool = false) {
        if pauseFirst { pause() }
        submit { [self] in await finishEnvironment(); _ = await perform(params) }
    }
    func tap(_ id: String) {
        if combo { if selectedKeys.contains(id) { selectedKeys.remove(id) } else { selectedKeys.insert(id) }; return }
        tapKeys([id])
    }
    func sendCombo() {
        guard !selectedKeys.isEmpty else { status = "先在键盘上选择组合键。"; return }
        tapKeys(selectedKeys)
    }
    private func tapKeys(_ ids: Set<String>) {
        let held = physicalKeys, generation = epoch
        submit { [self] in
            await finishEnvironment()
            _ = await perform(["action":"keys", "keys":.array(held.union(ids).sorted().map(JSONValue.string))])
            _ = await perform(["action":"advance", "ms":50])
            _ = await perform(["action":"keys", "keys":.array(held.sorted().map(JSONValue.string))])
            if let result = await perform(["action":"advance", "ms":.int(inputSettleTime)]) {
                resumeQueryIfNeeded(result, generation:generation)
            }
        }
    }
    func setVirtualFn(_ held:Bool) {
        if held && !keyboardCaptureActive { setKeyboardCapture(true) }
        virtualFnHeld = held
        updatePhysicalMatrix()
    }
    private var inputSettleTime: Int { max(35,(environment["wake_latency_ms"]?.int ?? 0)+20) }
    private func resumeQueryIfNeeded(_ result:JSONValue,generation:Int) {
        if epoch == generation && result["battery"]["query_active"].flag && !playing { play() }
    }
    func setKeyboardCapture(_ active:Bool) {
        guard active != keyboardCaptureActive else { return }
        if active {
            guard ready && page == .workbench else { return }
            releaseKeys(); physicalInput = PhysicalKeyboardState()
            keyboardCaptureActive = true
            status = "实体键盘输入已开启 · ⌘. 停止"
        } else {
            keyboardCaptureActive = false
            releaseKeys()
        }
    }
    // The AppKit focus owner calls this same entry point tested by the controller harness.
    func handlePhysicalEvent(_ event:NSEvent) {
        guard keyboardCaptureActive else { return }
        if event.type == .keyDown && event.keyCode == 0x2f && event.modifierFlags.contains(.command) {
            setKeyboardCapture(false); return
        }
        if event.type == .flagsChanged && event.keyCode == 0x39 {
            tapKeys(["3,0"]); return // macOS reports Caps Lock as a toggle, not a held key.
        }
        guard physicalInput.consume(event,rightCommandIsFn:rightCommandIsFn) else { return }
        updatePhysicalMatrix()
    }
    private func updatePhysicalMatrix() {
        physicalKeys = physicalInput.matrixKeys.union(virtualFnHeld ? ["7,14"] : [])
        let matrix = physicalKeys.sorted().map(JSONValue.string), generation = epoch
        submit { [self] in
            await finishEnvironment()
            _ = await perform(["action":"keys", "keys":.array(matrix)])
            if let result = await perform(["action":"advance", "ms":.int(inputSettleTime)]) {
                resumeQueryIfNeeded(result,generation:generation)
            }
        }
    }
    func queryBattery() {
        let current = epoch
        submit { [self] in await finishEnvironment(); if await perform(["action":"query"]) != nil, epoch == current, !playing { play() } }
    }
    func previewLighting(_ effect: Int) {
        let current = epoch
        submit { [self] in
            await finishEnvironment()
            if await perform(["action":"environment", "values":["main_off":false, "main_effect":.int(effect)]]) != nil {
                _ = await perform(["action":"render"])
                if epoch == current { play() }
            }
        }
    }
    func releaseKeys() {
        selectedKeys.removeAll(); physicalInput.releaseAll(); physicalKeys.removeAll(); virtualFnHeld = false
        submit { [self] in
            await finishEnvironment()
            guard await perform(["action":"keys", "keys": .array([])]) != nil,
                  await perform(["action":"advance", "ms":.int(inputSettleTime)]) != nil else { return }
            success(keyboardCaptureActive ? "所有按键已松开 · 实体键盘输入中" : "所有按键已松开")
        }
    }
    func pause() { playing = false; epoch += 1; clockTask?.cancel(); clockTask = nil }
    func togglePlayback() { playing ? pause() : play() }
    func setClock(_ mode: String) { let wasPlaying = playing; pause(); clockMode = mode; if wasPlaying { play() } }

    func prepareSideHID() { prepareSideHID(connection:"usb") }
    func prepareSideHID(connection:String) {
        guard ["usb","rf"].contains(connection), let image = images.filter({$0["side_hid_rf"].flag && !$0["firmware_version"].isNull}).max(by: {
            $0["firmware_version"].text.compare($1["firmware_version"].text, options: .numeric) == .orderedAscending
        }) else { issue = "内置带版本号的 USB + 2.4G 实验固件缺失，请重新构建应用。"; return }
        setKeyboardCapture(false); pause(); sideEditTask?.cancel(); sideEditTask = nil
        page = .sideHID
        submit { [self] in
            await finishEnvironment()
            var inputs = environment
            inputs["connection"] = .string(connection); inputs["cable"] = .bool(connection == "usb")
            inputs["receiver"] = true
            inputs["host_ready"] = true; inputs["host_suspended"] = false
            if await perform(["action":"reset","firmware":image["id"],"environment":.object(inputs)]) != nil {
                success("侧灯实验固件已载入 · 虚拟 \(connection == "rf" ? "2.4G" : "USB") 就绪")
            }
        }
    }
    private func sideHIDParameters(_ mode: String) -> JSONValue {
        ["action":"side_hid","mode":.string(mode),
         "colors":.array(sideHIDColors.map{.array($0.map{.int($0)})}),
         "brightness":.int(Int(sideHIDBrightness.rounded())),"period_ms":.int(Int((sideHIDPeriod*1000).rounded()))]
    }
    private func syncSideHIDControls() {
        let colors = sideHID["colors"].values.map{$0.values.map(\.int)}
        if colors.count == 9 && colors.allSatisfy({$0.count == 3}) { sideHIDColors = colors }
        sideHIDBrightness = sideHID["brightness"].double
        sideHIDPeriod = sideHID["period_ms"].double / 1000
    }
    func controlSideHID(_ mode:String, generation:Int? = nil) {
        sideEditTask?.cancel(); sideEditTask = nil
        let current = generation ?? epoch, params = sideHIDParameters(mode)
        submit { [self] in
            await finishEnvironment()
            if await perform(params) != nil {
                success(mode == "release" ? "已释放侧灯控制" : mode == "stop" ? "停止发送，等待固件超时恢复" : "侧灯 HID · 实时固件预览")
                if mode != "release" && mode != "stop" && epoch == current && !playing { play() }
            }
        }
    }
    private func queueSideHIDEdit(_ mode:String) {
        guard sideHIDReady else { return }
        sideEditTask?.cancel()
        let current = epoch
        sideEditTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds:60_000_000)
            guard let self, !Task.isCancelled else { return }
            controlSideHID(mode,generation:current)
        }
    }
    func setSideHIDColor(_ color:Color, segment:Int? = nil) {
        guard let rgb = NSColor(color).usingColorSpace(.sRGB) else { return }
        let values = [rgb.redComponent,rgb.greenComponent,rgb.blueComponent].map{max(0,min(255,Int(($0*255).rounded())))}
        if let segment { sideHIDColors[segment] = values }
        else { sideHIDColors = Array(repeating:values,count:9) }
        queueSideHIDEdit(segment == nil ? "solid" : "frame")
    }
    func sideHIDColorBinding(_ segment:Int? = nil) -> Binding<Color> {
        Binding(get:{ let rgb = self.sideHIDColors[segment ?? 0]; return Color(red:Double(rgb[0])/255,green:Double(rgb[1])/255,blue:Double(rgb[2])/255) },
                set:{ self.setSideHIDColor($0,segment:segment) })
    }
    func setSideHIDBrightness(_ value:Double) {
        sideHIDBrightness = value.rounded()
        if sideHID["mode"].text != "off" { queueSideHIDEdit(sideHID["mode"].text) }
    }
    func setSideHIDPeriod(_ value:Double) {
        sideHIDPeriod = value
        if sideHID["mode"].text == "rainbow" { queueSideHIDEdit("rainbow") }
    }
    func play() {
        guard ready, !playing, runtime["error"].isNull else { return }
        playing = true; epoch += 1; let generation = epoch; clockAt = ProcessInfo.processInfo.systemUptime
        clockTask = Task { [weak self] in
            guard let self else { return }
            while playing && generation == epoch && !Task.isCancelled {
                if !transportBusy && pendingCount == 0 {
                    let began = ProcessInfo.processInfo.systemUptime
                    let milliseconds = clockMode == "preview" ? min(5000, max(1, Int(((began-clockAt)*1000).rounded()))) : 20
                    let result = await perform(["action":.string(clockMode == "preview" ? "preview" : "advance"), "ms":.int(milliseconds)], background: true, generation: generation)
                    if result != nil, clockMode == "strict" { speed = Double(milliseconds)/max(1, (ProcessInfo.processInfo.systemUptime-began)*1000+16) }
                }
                try? await Task.sleep(nanoseconds: clockMode == "preview" ? 50_000_000 : 16_000_000)
            }
        }
    }
    func reset(_ firmware: String? = nil) {
        sideEditTask?.cancel(); sideEditTask = nil
        setKeyboardCapture(false)
        pause(); selectedKeys.removeAll(); physicalInput = PhysicalKeyboardState(); physicalKeys.removeAll(); issue = nil
        let inputs = environment
        for task in debounce.values { task.cancel() }
        debounce.removeAll(); envPending.removeAll()
        let image = firmware ?? selectedFirmware
        submit { [self] in if await perform(["action":"reset", "firmware":.string(image), "environment":.object(inputs)]) != nil { success("运行环境已重置") } }
    }
    func selectFirmware(_ value: String) { guard value != selectedFirmware else { return }; reset(value) }

    func setEnvironment(_ key: String, _ value: JSONValue, delayed: Bool = false) {
        environment[key] = value; envPending[key] = value; debounce[key]?.cancel()
        debounce[key] = Task { [weak self] in
            guard let self else { return }
            if delayed { try? await Task.sleep(nanoseconds: 180_000_000); if Task.isCancelled { return } }
            if key == "connection" || key == "keymap_preset" {
                sideEditTask?.cancel(); sideEditTask = nil
                setKeyboardCapture(false); selectedKeys.removeAll()
                pause()
                if key == "connection" { environment["cable"] = .bool(value.text == "usb") }
                _ = await perform(["action":"reset", "firmware":.string(selectedFirmware), "environment":.object(environment)])
            } else {
                if await perform(["action":"environment", "values":.object([key:value])]) != nil { _ = await perform(["action":"render"]) }
            }
            if envPending[key] == value { envPending.removeValue(forKey:key) }
            environment = runtime["environment"].objectValue.merging(envPending) { _, new in new }
        }
    }
    func boolBinding(_ key: String, inverted: Bool = false) -> Binding<Bool> {
        Binding(get: { inverted ? !(self.environment[key]?.flag ?? false) : self.environment[key]?.flag ?? false }, set: { self.setEnvironment(key, .bool(inverted ? !$0 : $0)) })
    }
    func intBinding(_ key: String, delayed: Bool = false) -> Binding<Double> {
        Binding(get: { self.environment[key]?.double ?? 0 }, set: { self.setEnvironment(key, .number($0.rounded()), delayed: delayed) })
    }
    func stringBinding(_ key: String) -> Binding<String> {
        Binding(get: { self.environment[key]?.text ?? "" }, set: { self.setEnvironment(key, .string($0)) })
    }

    func selectScenario(_ id: String) { selectedScenario = id; if let entry = scenarios.first(where: { $0["id"].text == id }) { scenarioJSON = entry.pretty } }
    func startJob(_ kind: String) {
        guard !jobRunning else { return }; pause(); issue = nil
        Task {
            do {
                await finishInput()
                var params: [String: JSONValue] = ["kind":.string(kind), "firmware":.string(selectedFirmware)]
                if kind == "scenario" || kind == "compare" { params["scenario"] = try JSONDecoder().decode(JSONValue.self, from: Data(scenarioJSON.utf8)) }
                if kind == "compare" || kind == "ota" { params["other"] = .string(comparisonFirmware) }
                if kind == "ota" { params["ota_case"] = .string(otaCase) }
                job = try await engine.call("start_job", .object(params))
                while jobRunning {
                    try await Task.sleep(nanoseconds: 180_000_000)
                    job = try await engine.call("job", ["id":job["id"]])
                }
                if job["status"].text == "error" { throw LabError(message: job["error"].text) }
                success("测试已完成：\(job["result"]["status"].text)")
            } catch { show(error) }
        }
    }
    func loadJob() { pause(); submit { [self] in await finishEnvironment(); if await perform(["action":"load_job", "job":job["id"]]) != nil { page = .workbench; success("场景末态已载入") } } }
    func readMemory() {
        Task {
            do {
                let result = try await capture("memory", ["address":.string(memoryAddress), "size":.int(memorySize)])
                let data = result["bytes"].text; var rows: [String] = []
                let bytes = stride(from: 0, to: data.count, by: 2).map { i -> String in let start = data.index(data.startIndex, offsetBy: i); return String(data[start..<data.index(start,offsetBy:2)]) }
                let base = UInt64(result["address"].text.dropFirst(2),radix:16) ?? 0
                for i in stride(from:0,to:bytes.count,by:16) { rows.append(String(format:"%08llX  ",base+UInt64(i)) + bytes[i..<min(i+16,bytes.count)].joined(separator:" ")) }
                memoryText = rows.joined(separator:"\n") + "\n\nPC " + result["pc"].text + "\n" + result["registers"].pretty
            } catch { show(error) }
        }
    }

    private func openFile(_ title: String, completion: @escaping (URL) -> Void) {
        setKeyboardCapture(false)
        let panel = NSOpenPanel(); panel.title = title; panel.canChooseDirectories = false; panel.allowsMultipleSelection = false
        if let window = NSApp.mainWindow { panel.beginSheetModal(for:window) { if $0 == .OK, let url = panel.url { completion(url) } } }
        else { panel.begin { if $0 == .OK, let url = panel.url { completion(url) } } }
    }
    func save(_ data: Data, name: String) {
        let panel = NSSavePanel(); panel.nameFieldStringValue = name
        let completion: (NSApplication.ModalResponse) -> Void = { [weak self] response in
            guard response == .OK, let url = panel.url else { return }
            do { try data.write(to:url,options:.atomic); self?.success("已保存 \(url.lastPathComponent)") }
            catch { self?.show(error) }
        }
        if let window = NSApp.mainWindow { panel.beginSheetModal(for:window,completionHandler:completion) } else { panel.begin(completionHandler:completion) }
    }
    func export(_ method: String, name: String) {
        if method == "snapshot" { pause() }
        Task {
            do {
                let result = try await capture(method)
                if name.hasSuffix(".ndjson") { save(Data((try result["trace"].values.map { String(data:try $0.data(),encoding:.utf8) ?? "" }.joined(separator:"\n")).utf8),name:name) }
                else { save(try result.data(pretty:true),name:name) }
            } catch { show(error) }
        }
    }
    func exportJob() { do { save(try job.data(pretty:true),name:"MQ80-test-result.json") } catch { show(error) } }
    func exportScenario() {
        do { let value = try JSONDecoder().decode(JSONValue.self,from:Data(scenarioJSON.utf8)); save(try value.data(pretty:true),name:"MQ80-scenario.json") } catch { show(error) }
    }
    func importFirmware() {
        sideEditTask?.cancel(); sideEditTask = nil
        pause()
        openFile("导入 CS836B 应用 BIN") { [weak self] url in
            guard let self else { return }
            Task {
                do {
                    await self.finishInput()
                    let bytes = try Data(contentsOf:url); guard bytes.count<=131072 else { throw LabError(message:"固件超过 128 KB。") }
                    let meta = try await self.engine.call("firmware", ["base64":.string(bytes.base64EncodedString()), "name":.string(url.lastPathComponent)])
                    try await self.loadBootstrap()
                    if await self.perform(["action":"reset", "firmware":meta["id"], "environment":.object(self.environment)]) != nil { self.success("固件已校验并载入；原文件未修改") }
                } catch { self.show(error) }
            }
        }
    }
    func importJSON(snapshot: Bool) {
        sideEditTask?.cancel(); sideEditTask = nil
        pause()
        openFile(snapshot ? "载入运行快照" : "导入场景 JSON") { [weak self] url in
            guard let self else { return }
            Task {
                do {
                    await self.finishInput()
                    let data = try Data(contentsOf:url); guard data.count <= 8*1024*1024 else { throw LabError(message:"JSON 超过 8 MB。") }
                    let value = try JSONDecoder().decode(JSONValue.self,from:data)
                    if snapshot { if await self.perform(["action":"restore", "snapshot":value]) != nil { self.success("快照已恢复") } }
                    else { self.scenarioJSON = value.pretty; self.success("场景已载入编辑器，尚未运行") }
                } catch { self.show(error) }
            }
        }
    }
}
