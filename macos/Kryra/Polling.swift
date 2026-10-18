import Foundation
import CoreFoundation

enum BatteryPolling {
    static let defaultSeconds = 60
    static let range = 30...3600
    static let presets = [30,60,120,300,600,1800,3600]
    static let defaultsKey = "batteryPollIntervalSeconds"
    static func stored(in defaults: UserDefaults) -> Int {
        guard let number = defaults.object(forKey:defaultsKey) as? NSNumber,
              CFGetTypeID(number) != CFBooleanGetTypeID(), number.doubleValue.isFinite,
              number.doubleValue.rounded() == number.doubleValue,
              (Double(range.lowerBound)...Double(range.upperBound)).contains(number.doubleValue) else { return defaultSeconds }
        return number.intValue
    }
    static func validate(_ seconds: Int) throws {
        guard range.contains(seconds) else { throw ControlError("电量更新间隔需要在 30～3600 秒之间。",status:400) }
    }
    static func parseSettings(_ value: [String:Any]) throws -> Int {
        guard Set(value.keys) == ["battery_poll_interval_seconds"],
              let number = value["battery_poll_interval_seconds"] as? NSNumber,
              CFGetTypeID(number) != CFBooleanGetTypeID(), number.doubleValue.isFinite,
              number.doubleValue.rounded() == number.doubleValue,
              (Double(range.lowerBound)...Double(range.upperBound)).contains(number.doubleValue) else {
            throw ControlError("请设置 battery_poll_interval_seconds，取 30～3600 的整数秒。",status:400)
        }
        return number.intValue
    }
    static func title(_ seconds: Int) -> String {
        if seconds % 3600 == 0 { return "\(seconds/3600) 小时" }
        if seconds % 60 == 0 { return "\(seconds/60) 分钟" }
        return "\(seconds) 秒"
    }
    static func staleAfter(_ seconds: Int) -> TimeInterval { Double(seconds)*2+5 }
    static func delay(seconds: Int, failures: Int, noDevices: Bool) -> TimeInterval {
        if noDevices { return 5 }
        // Never make a long user-selected interval shorter when backing off.
        return min(Double(max(300,seconds)),Double(seconds)*pow(2,Double(min(8,max(0,failures-1)))))
    }
}
