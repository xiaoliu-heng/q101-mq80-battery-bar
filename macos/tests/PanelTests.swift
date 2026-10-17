import AppKit
import SwiftUI

/// Renders only our own views, in hidden test windows. No screen capture/HID.
final class PanelBackend: HIDBackend {
    private let demo = DemoBackend()
    private let lock = NSLock()
    private var failed = false
    private var requests: [[UInt8]] = []
    func setFailed(_ value: Bool) { lock.lock(); failed = value; lock.unlock() }
    var count: Int { lock.lock(); defer { lock.unlock() }; return requests.count }
    func scan() throws -> [DeviceDescriptor] {
        [DeviceDescriptor(id:"panel-test",name:String(repeating:"很长的键盘名称",count:20),transport:"usb")]
    }
    func exchange(_ request: [UInt8], deviceID: String) throws -> [UInt8] {
        lock.lock(); defer { lock.unlock() }; requests.append(request)
        if failed { throw ControlError(String(repeating:"键盘连接中断，请检查连接或更新至固件 3.1.0。",count:16)) }
        return try demo.exchange(request,deviceID:deviceID)
    }
    func close() {}
}

@main @MainActor struct PanelTests {
    static var checks: [[String:Any]] = []
    static func check(_ name: String,_ result: Bool) throws {
        guard result else { throw ControlError("FAIL: \(name)") }
        checks.append(["name":name,"status":"PASS"])
    }
    static func settle() async { try? await Task.sleep(nanoseconds:100_000_000) }
    static func descendants(_ view: NSView) -> [NSView] { [view] + view.subviews.flatMap(descendants) }
    static func export(_ host: NSView, to path: URL) throws {
        host.layoutSubtreeIfNeeded()
        guard let rep = host.bitmapImageRepForCachingDisplay(in:host.bounds) else { throw ControlError("No bitmap") }
        host.cacheDisplay(in:host.bounds,to:rep)
        guard let png = rep.representation(using:.png,properties:[:]) else { throw ControlError("No PNG") }
        try png.write(to:path)
    }
    static func main() async {
        do {
            _ = NSApplication.shared; NSApp.setActivationPolicy(.prohibited)
            let output = URL(fileURLWithPath:CommandLine.arguments[1],isDirectory:true)
            try FileManager.default.createDirectory(at:output,withIntermediateDirectories:true)
            let app = AppModel(demo:true)
            app.skillRoot = URL(fileURLWithPath:"/"+String(repeating:"very-long-folder-name/",count:16)+"skills")
            let backend = PanelBackend()
            let keyboard = KeyboardController(worker:HIDWorker(factory:{backend}),demo:true,defaults:UserDefaults(suiteName:"Kryra.PanelTests.\(UUID())")!)
            _ = try await keyboard.refresh()
            let baseline = backend.count
            for scheme in [ColorScheme.light,.dark] {
                let theme = scheme == .light ? "light" : "dark"
                for page in [PanelPage.lighting,.settings] {
                    let pageName = page == .lighting ? "lighting" : "settings"
                    for effect in LightingEffect.allCases {
                        if page == .settings && effect != .solid { continue }
                        var draft = LightingDraft(); draft.select(effect)
                        let content = ControlPanel(app:app,keyboard:keyboard,initialPage:page,initialDraft:draft)
                            .environment(\.colorScheme,scheme)
                        let window = fixedPanelWindow(AnyView(content),title:"Kryra · 演示")
                        let host = window.contentView as! NSHostingView<AnyView>
                        window.appearance = NSAppearance(named:scheme == .light ? .aqua : .darkAqua)
                        await settle(); host.layoutSubtreeIfNeeded()
                        let key = "\(theme)-\(pageName)-\(effect.rawValue)"
                        try check("\(key) fixed content bounds",host.bounds.size == PanelMetrics.size)
                        try check("\(key) disables intrinsic host resizing",host.sizingOptions.isEmpty)
                        try check("\(key) no scrolling content or scroller",!descendants(host).contains { $0 is NSScrollView || $0 is NSScroller })
                        let size = window.frame.size
                        backend.setFailed(true); _ = try? await keyboard.refresh(); await settle()
                        host.layoutSubtreeIfNeeded()
                        try check("\(key) long error cannot change content/window size",host.bounds.size == PanelMetrics.size && window.frame.size == size)
                        backend.setFailed(false); _ = try await keyboard.refresh(); await settle()
                        try check("\(key) reconnect preserves width",host.bounds.width == PanelMetrics.width && window.frame.size == size)
                        try export(host,to:output.appendingPathComponent(key+".png"))
                        window.contentView = nil; window.close()
                    }
                }
            }
            // The hosting controller used by NSPopover has exactly the same contract.
            let controller = FixedPanelHostingController(content:ControlPanel(app:app,keyboard:keyboard))
            let popover = NSPopover(); popover.contentViewController = controller; popover.contentSize = PanelMetrics.size
            await settle()
            try check("popover preferred size is fixed",controller.preferredContentSize == PanelMetrics.size && controller.sizingOptions.isEmpty)
            try check("popover content is fixed",popover.contentSize == PanelMetrics.size)
            let before = backend.count
            for seconds in BatteryPolling.presets {
                try keyboard.setPollingInterval(seconds)
                try check("polling interval \(seconds) is configurable without HID",keyboard.pollIntervalSeconds == seconds && backend.count == before)
            }
            try keyboard.setPollingInterval(60)
            var draft = LightingDraft()
            for effect in LightingEffect.allCases {
                draft.select(effect); draft.brightness = 45; draft.seconds = 3
                _ = try draft.command(); _ = draft.samples(elapsed:0.5)
            }
            try check("preview/configuration never sends HID",backend.count == before)
            try check("preview renders keep hardware effects stopped",keyboard.command == nil && baseline >= 1)
            await keyboard.shutdown(); await app.keyboard.shutdown()
            #if KRYRA_GLASS_SDK
            let nativeGlassCompiled = true
            #else
            let nativeGlassCompiled = false
            #endif
            let report: [String:Any] = ["status":"PASS","checks":checks,"count":checks.count,
                "device_access":"NONE","content_size":["width":PanelMetrics.width,"height":PanelMetrics.height],
                "rendering":"NSHostingView cache of our own hidden test windows, light/dark, all four effects/settings",
                "native_glass_sdk_compiled":nativeGlassCompiled,
                "system_glass_26_tested":nativeGlassCompiled && ProcessInfo.processInfo.operatingSystemVersion.majorVersion >= 26]
            try JSONSerialization.data(withJSONObject:report,options:[.prettyPrinted,.sortedKeys]).write(to:output.appendingPathComponent("panel-validation.json"))
            print("PASS \(checks.count) panel checks; images: \(output.path)")
        } catch { fputs("\(error)\n",stderr); exit(1) }
    }
}
