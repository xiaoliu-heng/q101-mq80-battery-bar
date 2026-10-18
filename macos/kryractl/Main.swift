import Foundation
import AppKit

@main struct KryraCLI {
    static let help = """
    Kryra \(AppIdentity.version) — 本机键盘控制
      kryractl battery [--refresh]
      kryractl status
      kryractl settings [--poll-interval 60]
      kryractl solid FF8000 [--brightness 25] [--seconds 5]
      kryractl blink FF8000 [--period 1000] [--brightness 25] [--seconds 5]
      kryractl breathe 34D399 [--period 1000] [--brightness 25] [--seconds 5]
      kryractl rainbow [--period 5000] [--brightness 25] [--seconds 5]
      kryractl frame HEX HEX HEX HEX HEX HEX HEX HEX HEX [--brightness 25] [--seconds 5]
      kryractl release
      kryractl launch
    仅支持 v3.1.0 固件，包括电量查询。默认亮度 25%，5 秒后恢复原灯效。
    四种内置灯效仅发送一次，支持 0.1～60 秒；自定义渐变的九个颜色采样点支持 0.5～10 秒。
    没有持续发送或无限期模式；无线休眠可提前结束内置通知。
    可选 --request-id N 用于重试同一次内置通知；相同编号和参数不会重新计时。
    结果为 JSON；release 立即恢复键盘原灯效。
    """
    static func main() async {
        do {
            var args = Array(CommandLine.arguments.dropFirst())
            guard let command = args.first, command != "--help", command != "help" else { print(help); return }
            args.removeFirst()
            if command == "--version" { print(AppIdentity.version); return }
            if command == "launch" { try await launch(); print("{\"launched\":true}"); return }
            var method = "GET", route = "/v1/status", payload: [String:Any]?
            if command == "battery" {
                guard args.isEmpty || args == ["--refresh"] else { throw ControlError("battery 仅接受 --refresh。",status:400) }
                route = "/v1/battery" + (args.isEmpty ? "" : "?refresh=true")
            } else if command == "status" {
                guard args.isEmpty else { throw ControlError("status 不接受额外参数。",status:400) }
            } else if command == "settings" {
                route = "/v1/settings"
                if !args.isEmpty {
                    guard args.count == 2, args[0] == "--poll-interval", let seconds = Int(args[1]) else {
                        throw ControlError("settings 仅接受 --poll-interval 整数秒（30～3600）。",status:400)
                    }
                    try BatteryPolling.validate(seconds)
                    method = "POST"; payload = ["battery_poll_interval_seconds":seconds]
                }
            } else if ["solid","blink","breathe","frame","rainbow","release"].contains(command) {
                method = "POST"; route = "/v1/lighting"; var body: [String:Any] = ["mode":command]
                if ["solid","blink","breathe"].contains(command) {
                    guard let color = args.first else { throw ControlError("请指定六位颜色。",status:400) }
                    body["color"] = color; args.removeFirst()
                } else if command == "frame" {
                    guard args.count >= 9 else { throw ControlError("frame 需要九个颜色。",status:400) }
                    body["colors"] = Array(args.prefix(9)); args.removeFirst(9)
                }
                var seen = Set<String>()
                while !args.isEmpty {
                    let option = args.removeFirst()
                    guard seen.insert(option).inserted else { throw ControlError("参数重复。",status:400) }
                    if option == "--continuous" { body["continuous"] = true; continue }
                    guard let value = args.first else { throw ControlError("参数缺少值。",status:400) }
                    args.removeFirst()
                    switch option {
                    case "--brightness": guard let n = Int(value) else { throw ControlError("亮度需要整数。",status:400) }; body["brightness"] = n
                    case "--period": guard let n = Int(value) else { throw ControlError("周期需要整数毫秒。",status:400) }; body["period_ms"] = n
                    case "--seconds": guard let n = Double(value) else { throw ControlError("时间需要数字秒。",status:400) }; body["duration_seconds"] = n
                    case "--request-id": guard let n = UInt32(value) else { throw ControlError("请求编号需要 1～4294967295。",status:400) }; body["request_id"] = n
                    default: throw ControlError("未知参数：\(option)",status:400)
                    }
                }
                _ = try LightingCommand.parse(body); payload = body
            } else { throw ControlError("未知命令。\n\(help)",status:400) }
            let config = try RuntimeFiles.readAPI()
            let port = config["port"] as! Int, token = config["token"] as! String
            guard let url = URL(string:"http://127.0.0.1:\(port)\(route)") else { throw ControlError("API 地址无效。") }
            var request = URLRequest(url:url); request.httpMethod = method; request.timeoutInterval = 6
            request.setValue("Bearer \(token)",forHTTPHeaderField:"Authorization")
            if let payload {
                request.httpBody = try JSONSerialization.data(withJSONObject:payload)
                request.setValue("application/json",forHTTPHeaderField:"Content-Type")
            }
            let configuration = URLSessionConfiguration.ephemeral
            configuration.connectionProxyDictionary = [:]
            let session = URLSession(configuration:configuration)
            defer { session.invalidateAndCancel() }
            let (data,response) = try await session.data(for:request)
            guard let response = response as? HTTPURLResponse else { throw ControlError("本机 API 回复无效。") }
            let value = try JSONSerialization.jsonObject(with:data)
            let output = try JSONSerialization.data(withJSONObject:value,options:[.prettyPrinted,.sortedKeys,.withoutEscapingSlashes])
            print(String(decoding:output,as:UTF8.self))
            if response.statusCode != 200 { exit(1) }
        } catch {
            let value: [String:Any] = ["error":["message":error.localizedDescription]]
            if let data = try? JSONSerialization.data(withJSONObject:value,options:[.sortedKeys]) { FileHandle.standardError.write(data + Data([10])) }
            exit(1)
        }
    }
    @MainActor static func launch() async throws {
        var path = NSWorkspace.shared.urlForApplication(withBundleIdentifier:AppIdentity.bundleID)
        if let data = try? Data(contentsOf:RuntimeFiles.directory.appendingPathComponent("installation.json")),
           let value = try? JSONSerialization.jsonObject(with:data) as? [String:Any],
           let candidate = value["app_path"] as? String, FileManager.default.fileExists(atPath:candidate) { path = URL(fileURLWithPath:candidate) }
        guard let path else { throw ControlError("未找到 Kryra，请先将应用放入应用程序目录并打开一次。") }
        let config = NSWorkspace.OpenConfiguration(); config.activates = false
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void,Error>) in
            NSWorkspace.shared.openApplication(at:path,configuration:config) { _, error in
                if let error { continuation.resume(throwing:error) } else { continuation.resume() }
            }
        }
        for _ in 0..<20 {
            if (try? RuntimeFiles.readAPI()) != nil { return }
            try await Task.sleep(nanoseconds:100_000_000)
        }
        throw ControlError("应用已打开，本机 API 正在启动，请稍后重试。")
    }
}
