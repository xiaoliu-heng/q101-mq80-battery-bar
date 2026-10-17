import Foundation
import CoreFoundation

struct ControlError: LocalizedError {
    let message: String
    let status: Int
    init(_ message: String, status: Int = 503) { self.message = message; self.status = status }
    var errorDescription: String? { message }
}

struct RGB: Equatable {
    let r: UInt8, g: UInt8, b: UInt8
    init(_ r: UInt8, _ g: UInt8, _ b: UInt8) { self.r = r; self.g = g; self.b = b }
    init(hex: String) throws {
        let value = hex.hasPrefix("#") ? String(hex.dropFirst()) : hex
        guard value.utf8.count == 6, value.utf8.allSatisfy({ (48...57).contains($0) || (65...70).contains($0) || (97...102).contains($0) }),
              let n = UInt32(value, radix: 16) else { throw ControlError("颜色需要六位十六进制，例如 FF8000。", status: 400) }
        self.init(UInt8(n >> 16), UInt8((n >> 8) & 255), UInt8(n & 255))
    }
    var hex: String { String(format: "%02X%02X%02X", r, g, b) }
    func scaled(_ brightness: Int) -> [UInt8] { [r, g, b].map { UInt8(Int($0) * brightness / 100) } }
    static func rainbow(_ hue: Double) -> RGB {
        let h = (hue - floor(hue)) * 6, i = Int(h)
        let f = h - Double(i), q = 1 - f
        let values: [Double]
        switch i { case 0: values = [1,f,0]; case 1: values = [q,1,0]; case 2: values = [0,1,f]
        case 3: values = [0,q,1]; case 4: values = [f,0,1]; default: values = [1,0,q] }
        return RGB(UInt8((values[0]*255).rounded()), UInt8((values[1]*255).rounded()), UInt8((values[2]*255).rounded()))
    }
}

enum Wire {
    enum Command: UInt8 { case info = 0, frame, release, keepalive, timeout, play }
    static let magic: [UInt8] = [0xfe, 0x53, 0x4c]
    static func packet(_ command: Command, sequence: UInt8, payload: [UInt8] = []) throws -> [UInt8] {
        guard payload.count <= 27, command != .frame || payload.count == 27 else { throw ControlError("一帧需要九组 RGB。", status: 400) }
        guard command != .play || payload.count == 13 else { throw ControlError("灯效参数需要 13 字节。",status:400) }
        return magic + [0x10 | command.rawValue, sequence] + payload + Array(repeating: 0, count: 27-payload.count)
    }
    static func matches(_ request: [UInt8], _ reply: [UInt8]) -> Bool {
        guard request.count == 32, reply.count == 32, reply[4] == request[4] else { return false }
        return (Array(reply[0..<3]) == magic && reply[3] == request[3] | 8) || (reply[0] == 1 && reply[1] == request[1])
    }
    static func decode(_ request: [UInt8], _ reply: [UInt8]) throws -> FirmwareState {
        guard request.count == 32, reply.count == 32, Array(reply[0..<3]) == magic,
              Array(reply[17..<21]) == Array("Q101".utf8) else { throw ControlError("键盘尚未提供 Q101 控制协议，请使用已验证的 v3 固件。") }
        guard reply[3] == request[3] | 8, reply[4] == request[4], reply[6] == 1 else { throw ControlError("键盘回复的命令、序号或协议版本不匹配。") }
        let errors = ["", "协议版本不支持", "未知命令", "参数无效", "灯光控制已过期", "当前连接不允许控灯", "报文长度错误"]
        guard reply[5] == 0 else { throw ControlError(Int(reply[5]) < errors.count ? errors[Int(reply[5])] : "未知固件错误") }
        guard reply[7] == 9, reply[21] == 162, reply[12] & 3 != 0, reply[9] <= 100 else { throw ControlError("键盘返回的能力或电量数据无效。") }
        let versioned = Array(reply[23..<26]) == [0x46,0x56,1]
        return FirmwareState(percent: Int(reply[9]), connection: reply[10] == 2 ? "usb" : reply[10] == 1 ? "rf" : "ble",
            active: reply[8] & 1 != 0, batteryQuery: reply[8] & 2 != 0,
            usbControl: reply[12] & 1 != 0, rfControl: reply[12] & 2 != 0,
            timeoutMS: Int(reply[13]) | Int(reply[14]) << 8, remainingMS: Int(reply[15]) | Int(reply[16]) << 8,
            version: versioned ? "\(reply[26]).\(reply[27]).\(reply[28])" : nil,
            build: versioned ? Int(reply[30]) | Int(reply[31]) << 8 : nil,
            nativeEffects: versioned && reply[29] & 16 != 0 && reply[22] & 16 != 0)
    }
}

struct FirmwareState {
    let percent: Int, connection: String
    let active: Bool, batteryQuery: Bool, usbControl: Bool, rfControl: Bool
    let timeoutMS: Int, remainingMS: Int
    let version: String?, build: Int?
    let nativeEffects: Bool
    func permits(_ transport: String) -> Bool { connection == transport && (transport == "usb" ? usbControl : transport == "rf" && rfControl) }
}

struct LightingCommand: Equatable {
    static let defaultBrightness = 25
    static let defaultDuration = 5.0
    let mode: String
    let colors: [RGB]
    let brightness: Int
    let periodMS: Int
    let duration: Double?
    let requestID: UInt32?
    static func parse(_ value: [String: Any]) throws -> LightingCommand {
        let known: Set<String> = ["mode","color","colors","brightness","period_ms","duration_seconds","continuous","request_id"]
        guard Set(value.keys).isSubset(of: known), let mode = value["mode"] as? String,
              ["solid","blink","breathe","frame","rainbow","release"].contains(mode) else { throw ControlError("需要 mode: solid、blink、breathe、rainbow、frame 或 release；请检查参数名称。", status: 400) }
        func integer(_ key: String, _ fallback: Int, _ range: ClosedRange<Int>) throws -> Int {
            guard let raw = value[key] else { return fallback }
            guard let n = raw as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID(), n.doubleValue.isFinite,
                  n.doubleValue.rounded() == n.doubleValue, range.contains(n.intValue) else { throw ControlError("\(key) 超出有效范围。", status: 400) }
            return n.intValue
        }
        let brightness = try integer("brightness", defaultBrightness, 0...100), period = try integer("period_ms", mode == "rainbow" ? 5000 : 1000, 500...20000)
        var continuous = false
        if let raw = value["continuous"] {
            guard let n = raw as? NSNumber, CFGetTypeID(n) == CFBooleanGetTypeID() else { throw ControlError("continuous 需要布尔值。",status:400) }
            continuous = n.boolValue
        }
        guard !continuous else { throw ControlError("通知必须指定有限时长；请使用 duration_seconds（最多 60 秒）。",status:400) }
        var duration: Double? = mode == "release" ? nil : defaultDuration
        if let raw = value["duration_seconds"] {
            guard let n = raw as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID(), n.doubleValue.isFinite,
                  n.doubleValue >= 0.1, n.doubleValue <= 60 else { throw ControlError("duration_seconds 需要在 0.1～60 秒之间。", status: 400) }
            duration = n.doubleValue
        }
        if mode == "frame", let duration, !(0.5...10).contains(duration) { throw ControlError("九段静态颜色支持 0.5～10 秒；四种内置灯效支持最多 60 秒。",status:400) }
        var colors = Array(repeating: RGB(255,128,0), count: 9)
        if ["solid","blink","breathe"].contains(mode) {
            guard let hex = value["color"] as? String, value["colors"] == nil else { throw ControlError("纯色需要 color 参数。", status: 400) }
            colors = Array(repeating: try RGB(hex: hex), count: 9)
        } else if mode == "frame" {
            guard let hexes = value["colors"] as? [String], hexes.count == 9, value["color"] == nil else { throw ControlError("逐段颜色需要九个十六进制 colors。", status: 400) }
            colors = try hexes.map { try RGB(hex: $0) }
        } else if value["color"] != nil || value["colors"] != nil { throw ControlError("该模式不接受颜色参数。", status: 400) }
        if mode == "release", value.count != 1 { throw ControlError("release 仅接受 mode 参数。", status: 400) }
        let requestID = value["request_id"] == nil ? nil : UInt32(try integer("request_id",1,1...Int(UInt32.max)))
        if mode == "frame", requestID != nil { throw ControlError("九段旧协议不支持 request_id。",status:400) }
        return LightingCommand(mode: mode, colors: colors, brightness: brightness, periodMS: period, duration: duration, requestID:requestID)
    }
    func payload(elapsed: Double) -> [UInt8] {
        let pixels = mode == "rainbow" ? (0..<9).map { RGB.rainbow(Double($0)/9 + elapsed*1000/Double(periodMS)) } : colors
        let phase = (elapsed*1000).truncatingRemainder(dividingBy:Double(periodMS))/Double(periodMS)
        let triangle = phase < 0.5 ? phase*2 : (1-phase)*2
        let level = mode == "blink" ? (phase < 0.5 ? 1.0 : 0.0) : mode == "breathe" ? triangle*triangle : 1.0
        return pixels.flatMap { $0.scaled(Int(Double(brightness)*level)) }
    }
    var nativeEffectID: UInt8? { ["solid":UInt8(1),"blink":2,"breathe":3,"rainbow":4][mode] }
    func nativePayload(requestID: UInt32) throws -> [UInt8] {
        guard let id = nativeEffectID, let duration, requestID != 0 else { throw ControlError("本地灯效参数无效。",status:400) }
        let rgb: [UInt8] = mode == "rainbow" ? [0,0,0] : [colors[0].r,colors[0].g,colors[0].b]
        let period = mode == "solid" ? 0 : periodMS
        let milliseconds = Int(ceil(duration*1000))
        var payload: [UInt8] = [id,UInt8(brightness)]
        payload.append(contentsOf:rgb)
        payload.append(contentsOf:[UInt8(period & 255),UInt8(period >> 8),UInt8(milliseconds & 255),UInt8(milliseconds >> 8)])
        for shift in [0,8,16,24] { payload.append(UInt8((requestID >> shift) & 255)) }
        return payload
    }
}
