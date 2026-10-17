import Foundation

final class FakeBackend: HIDBackend {
    private let lock = NSLock()
    private let demo = DemoBackend()
    let transport: String
    var available = true, legacy = false, capable = true, nativeCapable = true
    var responseDelay: UInt32 = 0
    private var packets: [[UInt8]] = [], names: [String] = []
    private(set) var closed = false
    init(transport: String = "usb") { self.transport = transport }
    func scan() throws -> [DeviceDescriptor] {
        lock.lock();defer{lock.unlock()}
        return available ? [DeviceDescriptor(id:"fake",name:"Test",transport:transport)] : []
    }
    func exchange(_ packet: [UInt8],deviceID: String) throws -> [UInt8] {
        lock.lock();defer{lock.unlock()}
        guard available else { throw ControlError("测试断线") }
        packets.append(packet);names.append(Thread.current.name ?? "")
        if responseDelay > 0 { usleep(responseDelay) }
        if legacy { var reply=packet;reply[0]=1;return reply }
        var reply=try demo.exchange(packet,deviceID:deviceID)
        reply[10]=transport == "rf" ? 1 : 2
        if !capable { reply[12]=1 }
        if !nativeCapable { reply[22] = 15; reply[29] = 15 }
        return reply
    }
    var recorded: [[UInt8]] { lock.lock();defer{lock.unlock()};return packets }
    var threadNames: [String] { lock.lock();defer{lock.unlock()};return names }
    func setAvailable(_ value: Bool) { lock.lock();available=value;lock.unlock() }
    func close() { lock.lock();closed=true;lock.unlock() }
}

@main @MainActor struct CoreTests {
    static var checks: [[String:Any]] = []
    static func check(_ name: String,_ condition: @autoclosure () throws -> Bool) throws {
        guard try condition() else { throw ControlError("FAIL: \(name)") }
        checks.append(["name":name,"status":"PASS"])
    }
    static func rejects(_ name: String,_ action: () throws -> Void) throws {
        do { try action() } catch { checks.append(["name":name,"status":"PASS"]);return }
        throw ControlError("FAIL (accepted): \(name)")
    }
    static func delay(_ milliseconds: Int) async { try? await Task.sleep(nanoseconds:UInt64(milliseconds)*1_000_000) }
    static func main() async {
        do {
            let args=CommandLine.arguments
            let fixtures=try JSONSerialization.jsonObject(with:Data(contentsOf:URL(fileURLWithPath:args[1]))) as! [[String:Any]]
            for (index,row) in fixtures.enumerated() {
                let command=Wire.Command(rawValue:UInt8(row["command"] as! Int))!
                let sequence=UInt8(row["sequence"] as! Int), payload=(row["payload"] as! [Int]).map(UInt8.init)
                let request=try Wire.packet(command,sequence:sequence,payload:payload)
                let reply=(row["reply"] as! [Int]).map(UInt8.init), expected=row["state"] as! [String:Any]
                let state=try Wire.decode(request,reply)
                try check("firmware packet fixture \(index)",request == (row["request"] as! [Int]).map(UInt8.init))
                try check("firmware reply fixture \(index)",state.percent == expected["battery_percent"] as! Int && state.connection == expected["connection"] as! String && state.active == expected["active"] as! Bool && state.version == expected["firmware_version"] as? String && state.build == expected["firmware_build"] as? Int && state.nativeEffects == expected["native_effects"] as! Bool)
                if let params = row["lighting_input"] as? [String:Any] {
                    let id = UInt32(payload[9]) | UInt32(payload[10]) << 8 | UInt32(payload[11]) << 16 | UInt32(payload[12]) << 24
                    try check("native payload matches executed firmware \(index)",try LightingCommand.parse(params).nativePayload(requestID:id) == payload)
                }
                if index == 0 {
                    for offset in [0,1,2,3,4,6,7,17,18,19,20,21] {
                        var bad=reply;bad[offset] ^= 1
                        try rejects("reject reply corruption at \(offset)") { _ = try Wire.decode(request,bad) }
                    }
                    for length in [0,1,31,33] { try rejects("reply length \(length)") { _ = try Wire.decode(request,length == 33 ? reply+[0] : Array(reply.prefix(length))) } }
                }
            }
            let colors=["000000","123456","FF8000","FFFFFF","00FF00","0000FF","ABCDEF","010101","FFFF00"]
            let frame=try LightingCommand.parse(["mode":"frame","colors":colors,"brightness":50])
            let expected=try colors.flatMap { try RGB(hex:$0).scaled(50) }
            try check("nine colors and brightness preserve byte order",frame.payload(elapsed:0) == expected)
            let rainbow=try LightingCommand.parse(["mode":"rainbow","period_ms":5000])
            try check("rainbow phase repeats",rainbow.payload(elapsed:0) == rainbow.payload(elapsed:5))
            for value: [String:Any] in [["mode":"solid","color":"xyz"],["mode":"solid","color":"FFFFFF","brightness":true],
                                      ["mode":"solid","color":"FFFFFF","brightness":101],["mode":"rainbow","period_ms":499],
                                      ["mode":"rainbow","duration_seconds":0],["mode":"rainbow","duration_seconds":Double.infinity],
                                      ["mode":"rainbow","continuous":1],["mode":"rainbow","continuous":"true"],
                                      ["mode":"rainbow","continuous":true,"duration_seconds":5],
                                      ["mode":"release","brightness":10],["mode":"frame","colors":["FFFFFF"]],
                                      ["mode":"rainbow","typo":1]] {
                try rejects("invalid lighting parameters \(checks.count)") { _ = try LightingCommand.parse(value) }
            }
            for id: Any in [true,0,-1,4294967296] {
                try rejects("invalid native request ID \(id)") { _ = try LightingCommand.parse(["mode":"rainbow","request_id":id]) }
            }
            let token=String(repeating:"a",count:64)
            let raw="POST /v1/lighting HTTP/1.1\r\nHost: 127.0.0.1:12345\r\nAuthorization: Bearer \(token)\r\nContent-Type: application/json\r\nContent-Length: 18\r\n\r\n{\"mode\":\"release\"}"
            let data=Data(raw.utf8)
            for n in [1,15,data.count-1] { try check("partial HTTP read \(n)",try HTTPRequest.parse(Data(data.prefix(n))) == nil) }
            let request=try HTTPRequest.parse(data)!
            try check("HTTP request body and token",request.authorized(token:token,port:12345) && (try request.json())["mode"] as? String == "release")
            try check("wrong token refused",!request.authorized(token:String(repeating:"b",count:64),port:12345))
            let origin=raw.replacingOccurrences(of:"Host:",with:"Origin: https://example.org\r\nHost:")
            try check("browser Origin refused",!(try HTTPRequest.parse(Data(origin.utf8))!).authorized(token:token,port:12345))
            for invalid in [raw+"x",raw.replacingOccurrences(of:"Content-Length: 18",with:"Content-Length: 18\r\nContent-Length: 18"),
                            raw.replacingOccurrences(of:"Content-Length: 18",with:"Transfer-Encoding: chunked"),
                            raw.replacingOccurrences(of:"Content-Length: 18",with:"Content-Length: 99999"),
                            "GET / HTTP/1.1\r\nX: "+String(repeating:"a",count:9000)] {
                try rejects("HTTP bounds and ambiguity \(checks.count)") { _ = try HTTPRequest.parse(Data(invalid.utf8)) }
            }
            let fake=FakeBackend(), gateway=DeviceGateway(FakeBackend())
            try rejects("controls cannot precede INFO") { _ = try gateway.command(.frame,payload:Array(repeating:255,count:27)) }
            let legacy=FakeBackend();legacy.legacy=true
            try rejects("old firmware rejected before control") { _ = try DeviceGateway(legacy).connect(selection:"auto") }
            try check("legacy received only INFO",legacy.recorded.count == 1 && legacy.recorded[0][3] == 0x10)
            let worker=HIDWorker(factory:{fake})
            let suite=UserDefaults(suiteName:"Kryra.tests.\(UUID())")!
            let keyboard=KeyboardController(worker:worker,defaults:suite)
            _ = try await keyboard.refresh()
            _ = try await keyboard.refresh()
            try check("battery reads never acquire lighting",fake.recorded.allSatisfy { $0[3] == 0x10 } && !keyboard.stale && keyboard.battery == 88)
            _ = try await keyboard.setLighting(LightingCommand.parse(["mode":"solid","color":"0080FF","brightness":50,"duration_seconds":0.35]))
            await delay(700)
            try check("firmware deadline clears UI without a release packet",keyboard.command == nil && keyboard.mode == "original" && !fake.recorded.contains { $0[3] == 0x12 })
            let plays=fake.recorded.filter { $0[3] == 0x15 }
            try check("native controller sends color and brightness as parameters",Array(plays[0][5..<10]) == [1,50,0,128,255])
            _ = try await keyboard.setLighting(LightingCommand.parse(["mode":"rainbow","duration_seconds":0.35]))
            await delay(120)
            _ = try await keyboard.setLighting(LightingCommand.parse(["mode":"solid","color":"00FF00","duration_seconds":2]))
            let before=fake.recorded.count
            await delay(1250)
            try check("superseded timer cannot cancel newer effect",keyboard.mode == "solid" && fake.recorded.count == before)
            try check("native effects never stream or renew",!fake.recorded.contains { [0x11,0x13,0x14].contains($0[3]) })
            await keyboard.suspend()
            let stopped=fake.recorded.count
            await delay(400)
            try check("computer sleep stops lighting traffic",keyboard.sleeping && keyboard.command == nil && fake.recorded.count == stopped)
            await keyboard.resume()
            _ = try await keyboard.setLighting(LightingCommand.parse(["mode":"solid","color":"FFFFFF","duration_seconds":2]))
            fake.setAvailable(false)
            do { _ = try await keyboard.refresh() } catch {}
            try check("disconnect preserves stale battery without restarting control",!keyboard.connected && keyboard.stale && keyboard.battery == 88 && keyboard.command == nil)
            fake.setAvailable(true);let reconnect=fake.recorded.count
            _ = try await keyboard.refresh();await delay(300)
            try check("reconnect only reads state",fake.recorded.dropFirst(reconnect).allSatisfy { $0[3] == 0x10 })
            try check("all HID work stays on named serial thread",Set(fake.threadNames) == ["Kryra Raw HID"])
            _ = try await keyboard.setLighting(LightingCommand.parse(["mode":"solid","color":"FF0000"]))
            await keyboard.shutdown()
            try check("shutdown releases owned lighting",fake.recorded.last?[3] == 0x12)
            try await rfAndOwnershipTests()
            try await notificationPowerTests()
            try await installerTests()
            let result: [String:Any] = ["status":"PASS","checks":checks,"count":checks.count,"device_access":"NONE"]
            try JSONSerialization.data(withJSONObject:result,options:[.prettyPrinted,.sortedKeys]).write(to:URL(fileURLWithPath:args[2]))
            print("PASS \(checks.count) native checks")
        } catch { fputs("\(error)\n",stderr);exit(1) }
    }
    static func rfAndOwnershipTests() async throws {
        let limited=FakeBackend(transport:"rf");limited.capable=false
        let gateway=DeviceGateway(limited)
        let reading=try gateway.connect(selection:"auto")
        try check("legacy RF can read battery without claiming control",reading.state.percent == 88 && !reading.state.rfControl)
        try rejects("legacy RF cannot receive lighting frame") { _ = try gateway.command(.frame,payload:Array(repeating:255,count:27)) }
        try check("unsupported RF only received INFO",limited.recorded.count == 1)
        let rf=FakeBackend(transport:"rf")
        let controller=KeyboardController(worker:HIDWorker(factory:{rf}),defaults:UserDefaults(suiteName:"KryraRFTests.\(UUID())")!)
        _ = try await controller.refresh()
        _ = try await controller.setLighting(LightingCommand.parse(["mode":"rainbow","duration_seconds":0.36]))
        await delay(550)
        let plays=rf.recorded.filter{$0[3] == 0x15}.count
        try check("RF rainbow sends one local effect command",plays == 1 && !rf.recorded.contains{$0[3] == 0x11} && controller.command == nil)
        try check("RF finite animation needs no finishing traffic",rf.recorded.last?[3] == 0x15)
        await controller.shutdown()
        let external=FakeBackend()
        _ = try external.exchange(Wire.packet(.frame,sequence:4,payload:Array(repeating:127,count:27)),deviceID:"fake")
        let observing=KeyboardController(worker:HIDWorker(factory:{external}),defaults:UserDefaults(suiteName:"KryraObserveTests.\(UUID())")!)
        _ = try await observing.refresh()
        try check("external lighting is recognized",observing.mode == "external")
        await observing.select("auto")
        await observing.shutdown()
        try check("selecting and quitting do not release another controller",!external.recorded.contains{$0[3] == 0x12})
        let slow=FakeBackend();slow.responseDelay=30_000
        let suspended=KeyboardController(worker:HIDWorker(factory:{slow}),defaults:UserDefaults(suiteName:"KryraSlowTests.\(UUID())")!)
        let inFlight=Task { try await suspended.refresh() }
        await delay(5);await suspended.suspend()
        do { _ = try await inFlight.value; throw ControlError("late refresh incorrectly accepted") }
        catch { try check("in-flight refresh cannot undo computer sleep",suspended.sleeping && !suspended.connected) }
        await suspended.shutdown()
        let cancelledBackend=FakeBackend();cancelledBackend.responseDelay=40_000
        let cancelled=KeyboardController(worker:HIDWorker(factory:{cancelledBackend}),defaults:UserDefaults(suiteName:"KryraCancelTests.\(UUID())")!)
        let request=try LightingCommand.parse(["mode":"solid","color":"FF0000"])
        let operation=Task { try await cancelled.setLighting(request) }
        await delay(5);operation.cancel()
        do { _ = try await operation.value;throw ControlError("cancelled request completed") }
        catch { try check("cancelled request cannot apply queued lighting",!cancelledBackend.recorded.contains{$0[3] == 0x15}) }
        await cancelled.shutdown()
        let queuedBackend=FakeBackend(), queuedWorker=HIDWorker(factory:{queuedBackend})
        _ = try await queuedWorker.perform { try $0.connect(selection:"auto") }
        let blocker=Task { try await queuedWorker.perform { _ in usleep(60_000) } }
        await delay(5)
        let queuedFrame=Task { try await queuedWorker.perform { try $0.command(.frame,payload:Array(repeating:255,count:27)) } }
        await delay(5);queuedFrame.cancel()
        _ = try await blocker.value
        do { _ = try await queuedFrame.value; throw ControlError("cancelled frame ran") }
        catch { try check("a cancelled queued HID frame never runs",!queuedBackend.recorded.contains{$0[3] == 0x11}) }
        queuedWorker.stop()
    }
    static func notificationPowerTests() async throws {
        let rf=FakeBackend(transport:"rf")
        let controller=KeyboardController(worker:HIDWorker(factory:{rf}),defaults:UserDefaults(suiteName:"KryraPowerTests.\(UUID())")!)
        let state = try await controller.setLighting(LightingCommand.parse(["mode":"solid","color":"FFFFFF"]))
        let play=rf.recorded.first{$0[3] == 0x15}!
        try check("default notification sends brightness and five-second lifetime atomically",Array(play[5..<10]) == [1,25,255,255,255] && Int(play[12]) | Int(play[13]) << 8 == 5000)
        try check("native RF notification reports that it yields to idle sleep",(state["lighting"] as! [String:Any])["prevents_idle_sleep"] as? Bool == false)
        let count=rf.recorded.count
        await delay(2300)
        try check("native notification holds beyond former two-second lease without traffic",controller.mode == "solid" && rf.recorded.count == count)
        await delay(3000)
        try check("firmware-owned deadline finishes without more HID packets",controller.command == nil && controller.mode == "original" && rf.recorded.count == count)
        for effect in ["blink","breathe","rainbow"] {
            var params: [String:Any] = ["mode":effect,"duration_seconds":0.2]
            if effect != "rainbow" { params["color"] = "34D399" }
            let before=rf.recorded.count
            _ = try await controller.setLighting(LightingCommand.parse(params))
            await delay(350)
            try check("\(effect) completes with one INFO and one PLAY",rf.recorded.count == before+2 && rf.recorded.last?[3] == 0x15 && controller.command == nil)
        }
        try rejects("unbounded continuous control is rejected") { _ = try LightingCommand.parse(["mode":"rainbow","continuous":true]) }
        let retry = try LightingCommand.parse(["mode":"blink","color":"34D399","duration_seconds":0.8,"request_id":73])
        _ = try await controller.setLighting(retry)
        await delay(300)
        _ = try await controller.setLighting(retry)
        try check("retry preserves firmware phase in the local preview",controller.effectElapsed > 0.2)
        await delay(650)
        try check("retry does not postpone local expiry",controller.command == nil && controller.mode == "original")
        try rejects("notifications over one minute are rejected") { _ = try LightingCommand.parse(["mode":"rainbow","duration_seconds":60.001]) }
        let old=FakeBackend();old.nativeCapable=false
        let older=KeyboardController(worker:HIDWorker(factory:{old}),defaults:UserDefaults(suiteName:"KryraOldTests.\(UUID())")!)
        do { _ = try await older.setLighting(LightingCommand.parse(["mode":"rainbow"])); throw ControlError("old firmware received an animation") }
        catch { try check("old firmware receives only INFO and no streaming fallback",old.recorded.count == 1 && old.recorded[0][3] == 0x10 && older.command == nil) }
        await older.shutdown()
        await controller.shutdown()
    }
    static func installerTests() async throws {
        let fm=FileManager.default, folder=fm.temporaryDirectory.appendingPathComponent("kryra-installer-tests-\(UUID())")
        defer { try? fm.removeItem(at:folder) }
        let resource=folder.appendingPathComponent("resource"), root=folder.appendingPathComponent("skills")
        try fm.createDirectory(at:resource.appendingPathComponent("scripts"),withIntermediateDirectories:true)
        try Data("---\nname: kryra\ndescription: test\n---\n".utf8).write(to:resource.appendingPathComponent("SKILL.md"))
        let cli=resource.appendingPathComponent("scripts/kryractl")
        try Data("#!/bin/sh\nexit 0\n".utf8).write(to:cli);try fm.setAttributes([.posixPermissions:0o755],ofItemAtPath:cli.path)
        let target=try SkillInstaller.install(resource:resource,root:root)
        try check("installer copies executable and metadata",fm.isExecutableFile(atPath:target.appendingPathComponent("scripts/kryractl").path))
        try Data("updated".utf8).write(to:resource.appendingPathComponent("SKILL.md"))
        _ = try SkillInstaller.install(resource:resource,root:root)
        try check("managed skill can be updated",try String(contentsOf:target.appendingPathComponent("SKILL.md")) == "updated")
        try fm.removeItem(at:target.appendingPathComponent(".kryra.json"))
        try rejects("unmanaged skill is preserved") { _ = try SkillInstaller.install(resource:resource,root:root) }
        try check("foreign skill contents unchanged",try String(contentsOf:target.appendingPathComponent("SKILL.md")) == "updated")
    }
}
