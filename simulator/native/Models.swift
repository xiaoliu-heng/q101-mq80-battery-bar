import Foundation
import SwiftUI

enum JSONValue: Codable, Hashable {
    case object([String: JSONValue]), array([JSONValue]), string(String), number(Double), bool(Bool), null
    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let v = try? c.decode(Bool.self) { self = .bool(v) }
        else if let v = try? c.decode(Double.self) { self = .number(v) }
        else if let v = try? c.decode(String.self) { self = .string(v) }
        else if let v = try? c.decode([String: JSONValue].self) { self = .object(v) }
        else { self = .array(try c.decode([JSONValue].self)) }
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .object(let v): try c.encode(v)
        case .array(let v): try c.encode(v)
        case .string(let v): try c.encode(v)
        case .number(let v): try c.encode(v)
        case .bool(let v): try c.encode(v)
        case .null: try c.encodeNil()
        }
    }
    subscript(_ key: String) -> JSONValue { objectValue[key] ?? .null }
    var objectValue: [String: JSONValue] { if case .object(let v) = self { return v }; return [:] }
    var values: [JSONValue] { if case .array(let v) = self { return v }; return [] }
    var text: String { if case .string(let v) = self { return v }; return "" }
    var double: Double { if case .number(let v) = self { return v }; return 0 }
    var int: Int { Int(double) }
    var flag: Bool { if case .bool(let v) = self { return v }; return false }
    var isNull: Bool { self == .null }
    func data(pretty: Bool = false) throws -> Data {
        let e = JSONEncoder(); e.outputFormatting = pretty ? [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes] : [.withoutEscapingSlashes]
        return try e.encode(self)
    }
    var pretty: String { String(data: (try? data(pretty: true)) ?? Data(), encoding: .utf8) ?? "" }
    static func int(_ value: Int) -> JSONValue { .number(Double(value)) }
}
extension JSONValue: ExpressibleByStringLiteral, ExpressibleByIntegerLiteral, ExpressibleByBooleanLiteral, ExpressibleByDictionaryLiteral {
    init(stringLiteral value: String) { self = .string(value) }
    init(integerLiteral value: Int) { self = .number(Double(value)) }
    init(booleanLiteral value: Bool) { self = .bool(value) }
    init(dictionaryLiteral elements: (String, JSONValue)...) { self = .object(Dictionary(uniqueKeysWithValues: elements)) }
}

struct KeySpec: Decodable, Identifiable, Equatable {
    let id: String
    let row: Int
    let col: Int
    let x: Double
    let y: Double
    let w: Double
    let label: String
    let keycode: Int
}
struct Pixel: Equatable {
    let r: Int, g: Int, b: Int
    init(_ json: JSONValue) {
        let a = json.values; r = a.count > 0 ? a[0].int : 0; g = a.count > 1 ? a[1].int : 0; b = a.count > 2 ? a[2].int : 0
    }
    var isLit: Bool { r != 0 || g != 0 || b != 0 }
    var color: Color { Color(red: Double(r)/255, green: Double(g)/255, blue: Double(b)/255) }
    var nsColor: NSColor { NSColor(srgbRed: CGFloat(r)/255, green: CGFloat(g)/255, blue: CGFloat(b)/255, alpha: 1) }
    var description: String { "R \(r)  G \(g)  B \(b)" }
}
struct TraceRow: Identifiable {
    let value: JSONValue
    var id: String { "\(value["id"].int)-\(value["time_ms"].double)-\(value["kind"].text)" }
    var time: String { String(format: "%.3f", value["time_ms"].double) }
    var kind: String { value["kind"].text }
    var message: String { value["message"].text }
    var source: String {
        switch value["origin"].text {
        case "firmware": return "固件"
        case "firmware+model": return "混合"
        case "test": return "测试"
        case "cpu": return "CPU"
        case "checkpoint": return "检查点"
        default: return value["origin"].text.contains("model") ? "模型" : value["origin"].text
        }
    }
}
enum LabPage: String, CaseIterable, Identifiable {
    case workbench, lighting, scenarios, inspect, coverage
    var id: String { rawValue }
    var title: String {
        switch self { case .workbench: return "工作台"; case .lighting: return "灯效预览"; case .scenarios: return "场景与回归"; case .inspect: return "运行时检查"; case .coverage: return "建模覆盖" }
    }
    var icon: String {
        switch self { case .workbench: return "keyboard"; case .lighting: return "lightbulb.led.wide"; case .scenarios: return "checklist"; case .inspect: return "cpu"; case .coverage: return "square.stack.3d.up" }
    }
}

let effectNames = ["关闭", "波浪", "彩云", "漩涡", "混色", "呼吸", "常亮", "渐灭", "涟漪", "激光", "繁星", "花开", "穿梭", "波纹", "流星", "雨滴", "扫描", "触发色", "中心扩散"]
let monoFont = Font.system(.body, design: .monospaced)
