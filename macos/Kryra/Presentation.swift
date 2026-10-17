import Foundation

enum LightingEffect: String, CaseIterable, Identifiable {
    case solid, blink, breathe, rainbow
    var id: String { rawValue }
    var title: String {
        switch self { case .solid:return "纯色"; case .blink:return "闪烁"; case .breathe:return "呼吸"; case .rainbow:return "彩虹" }
    }
    var defaultPeriodMS: Int { self == .rainbow ? 5000 : 1000 }
}

/// A local preview. Editing it never starts or renews a keyboard notification.
struct LightingDraft: Equatable {
    var effect: LightingEffect = .rainbow
    var color = RGB(52,211,153)
    var brightness = LightingCommand.defaultBrightness
    var periodMS = 5000
    var seconds = LightingCommand.defaultDuration
    func command() throws -> LightingCommand {
        var value: [String:Any] = ["mode":effect.rawValue,"brightness":brightness,
                                  "period_ms":periodMS,"duration_seconds":seconds]
        if effect != .rainbow { value["color"] = color.hex }
        return try LightingCommand.parse(value)
    }
    mutating func select(_ effect: LightingEffect) {
        self.effect = effect; periodMS = effect.defaultPeriodMS
    }
    mutating func adopt(_ command: LightingCommand) {
        if let effect = LightingEffect(rawValue:command.mode) { self.effect = effect }
        color = command.colors[0]; brightness = command.brightness
        periodMS = command.periodMS; seconds = command.duration ?? LightingCommand.defaultDuration
    }
    func samples(elapsed: Double) -> [RGB] {
        if effect == .rainbow {
            return (0..<9).map { RGB.rainbow(Double($0)/9 + elapsed*1000/Double(periodMS)) }
        }
        return Array(repeating:color,count:9)
    }
    func intensity(elapsed: Double, reduceMotion: Bool) -> Double {
        let base = Double(brightness)/100
        if reduceMotion { return base }
        let phase = (max(0,elapsed)*1000).truncatingRemainder(dividingBy:Double(periodMS))/Double(periodMS)
        switch effect {
        case .blink:return phase < 0.5 ? base : 0
        case .breathe:
            let triangle = phase < 0.5 ? phase*2 : (1-phase)*2
            return base * triangle * triangle
        default:return base
        }
    }
}
