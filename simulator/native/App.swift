import AppKit
import Combine
import SwiftUI

@MainActor final class AppDelegate: NSObject, NSApplicationDelegate, NSToolbarDelegate {
    static var shutdown: (() -> Void)?
    private let lab = LabModel()
    private var window: NSWindow!
    private var titleObserver: AnyCancellable?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        installMenus()
        let available = NSScreen.main?.visibleFrame.size ?? NSSize(width:1440,height:1000)
        window = NSWindow(contentRect:NSRect(x:0,y:0,width:min(1440,available.width-60),height:min(920,available.height-60)),
                          styleMask:[.titled,.closable,.miniaturizable,.resizable],backing:.buffered,defer:false)
        window.title = "MQ80 Firmware Lab · 工作台"
        window.minSize = NSSize(width:1100,height:780)
        window.isReleasedWhenClosed = false
        showWorkbench()
        titleObserver = lab.$page.sink { [weak self] page in self?.window?.title = "MQ80 Firmware Lab · \(page.title)" }
        window.center(); window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps:true)
        lab.start()
    }
    @objc private func showWorkbench() {
        let hidden = ProcessInfo.processInfo.environment["MQ80_NATIVE_UI_HIDE_ACCESSIBILITY"] == "1"
        let host = NSHostingView(rootView:RootView(lab:lab).accessibilityHidden(hidden))
        host.sizingOptions = []
        host.frame = window.contentLayoutRect; host.autoresizingMask = [.width,.height]
        window.contentView = host
        let toolbar = NSToolbar(identifier:"MQ80NativeToolbar"); toolbar.delegate = self
        toolbar.displayMode = .iconOnly; window.toolbar = toolbar; window.toolbarStyle = .unified
    }
    func applicationShouldHandleReopen(_ sender:NSApplication,hasVisibleWindows flag:Bool) -> Bool { window?.makeKeyAndOrderFront(nil); return true }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender:NSApplication) -> Bool { true }
    func applicationWillTerminate(_ notification:Notification) { Self.shutdown?() }
    func toolbarDefaultItemIdentifiers(_ toolbar:NSToolbar) -> [NSToolbarItem.Identifier] { [.flexibleSpace,.init("labControls")] }
    func toolbarAllowedItemIdentifiers(_ toolbar:NSToolbar) -> [NSToolbarItem.Identifier] { [.flexibleSpace,.init("labControls")] }
    func toolbar(_ toolbar:NSToolbar,itemForItemIdentifier id:NSToolbarItem.Identifier,willBeInsertedIntoToolbar flag:Bool) -> NSToolbarItem? {
        guard id.rawValue == "labControls" else { return nil }
        let item = NSToolbarItem(itemIdentifier:id)
        let hidden = ProcessInfo.processInfo.environment["MQ80_NATIVE_UI_HIDE_ACCESSIBILITY"] == "1"
        item.view = NSHostingView(rootView:WindowControls(lab:lab).frame(width:440,height:30).accessibilityHidden(hidden))
        item.label = "固件与文件"; return item
    }
    private func item(_ title:String,_ action:Selector,_ key:String = "",shift:Bool = false) -> NSMenuItem {
        let entry = NSMenuItem(title:title,action:action,keyEquivalent:key); entry.target = self
        if shift { entry.keyEquivalentModifierMask = [.command,.shift] }
        return entry
    }
    private func installMenus() {
        let bar = NSMenu()
        func add(_ title:String,_ contents:[NSMenuItem]) -> NSMenu {
            let parent = NSMenuItem(title:title,action:nil,keyEquivalent:"")
            let menu = NSMenu(title:title); contents.forEach(menu.addItem); parent.submenu = menu; bar.addItem(parent); return menu
        }
        _ = add("MQ80 Firmware Lab",[
            item("关于 MQ80 Firmware Lab",#selector(about)),.separator(),
            NSMenuItem(title:"退出 MQ80 Firmware Lab",action:#selector(NSApplication.terminate(_:)),keyEquivalent:"q")])
        _ = add("文件",[item("导入固件…",#selector(importFirmware),"o"),.separator(),
            item("导出运行报告…",#selector(exportReport),"s"),item("保存快照…",#selector(saveSnapshot),"s",shift:true),
            item("载入快照…",#selector(loadSnapshot)),.separator(),item("导出操作录制…",#selector(exportRecording))])
        let edit = add("编辑",[])
        for (title,action,key) in [("撤销","undo:","z"),("剪切","cut:","x"),("复制","copy:","c"),("粘贴","paste:","v"),("全选","selectAll:","a")] {
            edit.addItem(NSMenuItem(title:title,action:Selector(action),keyEquivalent:key))
        }
        let sendA = item("发送 A",#selector(tapA),"a");sendA.keyEquivalentModifierMask = [.command,.option]
        _ = add("仿真",[item("继续 / 暂停",#selector(toggle),"r"),item("步进 1 ms",#selector(step)),
            item("Fn＋B 查询电量",#selector(query),"b",shift:true),sendA,
            item("开始 / 停止实体键盘输入",#selector(keyboardInput),"k",shift:true),
            item("松开所有键",#selector(releaseAll)),.separator(),
            item("全关灯 · 休眠检查点",#selector(sleepFast)),item("普通空闲 · 休眠检查点",#selector(sleepNormal)),
            .separator(),item("播放波浪灯效",#selector(wave)),item("播放常亮灯效",#selector(solid)),
            .separator(),item("准备 USB 侧灯测试",#selector(prepareSideHID)),item("准备 2.4G 侧灯测试",#selector(prepareSideHIDRF)),
            item("侧灯应用颜色",#selector(sideHIDSolid)),item("播放侧灯彩虹",#selector(sideHIDRainbow)),item("释放侧灯控制",#selector(sideHIDRelease)),
            .separator(),item("重置运行环境",#selector(reset)),item("运行全部回归",#selector(suite))])
        _ = add("显示",[item("工作台",#selector(workbench),"1"),item("灯效预览",#selector(lighting),"2"),
            item("场景与回归",#selector(scenarios),"3"),item("运行时检查",#selector(inspect),"4"),item("建模覆盖",#selector(coverage),"5"),
            item("侧灯 HID",#selector(sideHID),"6"),
            .separator(),item("显示 / 隐藏环境设置",#selector(inspector))])
        let windows = add("窗口",[NSMenuItem(title:"最小化",action:#selector(NSWindow.miniaturize(_:)),keyEquivalent:"m"),NSMenuItem(title:"缩放",action:#selector(NSWindow.performZoom(_:)),keyEquivalent:"")])
        NSApp.windowsMenu = windows
        let help = add("帮助",[item("MQ80 Firmware Lab 使用说明",#selector(guide))]); NSApp.helpMenu = help
        NSApp.mainMenu = bar
    }
    @objc private func about() { NSApp.orderFrontStandardAboutPanel(options:[.applicationVersion:"2.4.1",.credits:NSAttributedString(string:"原生 macOS 离线固件工作台 · 不连接或刷写键盘")]) }
    @objc private func importFirmware() { if lab.ready { lab.importFirmware() } }
    @objc private func exportReport() { if lab.ready { lab.export("report",name:"MQ80-runtime-report.json") } }
    @objc private func saveSnapshot() { if lab.ready { lab.export("snapshot",name:"MQ80-snapshot.json") } }
    @objc private func loadSnapshot() { if lab.ready { lab.importJSON(snapshot:true) } }
    @objc private func exportRecording() { if lab.ready { lab.export("recording",name:"MQ80-recording.json") } }
    @objc private func toggle() { if lab.ready { lab.togglePlayback() } }
    @objc private func step() { if lab.ready { lab.command(["action":"advance","ms":1],pauseFirst:true) } }
    @objc private func query() { if lab.ready { lab.queryBattery() } }
    @objc private func tapA() { if lab.ready { lab.tap("3,1") } }
    @objc private func releaseAll() { if lab.ready { lab.releaseKeys() } }
    @objc private func keyboardInput() { if lab.ready { lab.page = .workbench; lab.setKeyboardCapture(!lab.keyboardCaptureActive) } }
    @objc private func sleepFast() { if lab.ready { lab.command(["action":"sleep","gate":"fast"],pauseFirst:true) } }
    @objc private func sleepNormal() { if lab.ready { lab.command(["action":"sleep","gate":"normal"],pauseFirst:true) } }
    @objc private func wave() { if lab.ready { lab.page = .lighting;lab.previewLighting(1) } }
    @objc private func solid() { if lab.ready { lab.page = .lighting;lab.previewLighting(6) } }
    @objc private func prepareSideHID() { if lab.ready { lab.prepareSideHID() } }
    @objc private func prepareSideHIDRF() { if lab.ready { lab.prepareSideHID(connection:"rf") } }
    @objc private func sideHIDSolid() { if lab.ready { lab.controlSideHID("solid") } }
    @objc private func sideHIDRainbow() { if lab.ready { lab.controlSideHID("rainbow") } }
    @objc private func sideHIDRelease() { if lab.ready { lab.controlSideHID("release") } }
    @objc private func reset() { if lab.ready { lab.reset() } }
    @objc private func suite() { if lab.ready && !lab.jobRunning { lab.page = .scenarios;lab.startJob("suite") } }
    @objc private func inspector() { lab.showInspector.toggle() }
    @objc private func workbench() { lab.page = .workbench }
    @objc private func lighting() { lab.page = .lighting }
    @objc private func scenarios() { lab.page = .scenarios }
    @objc private func inspect() { lab.page = .inspect }
    @objc private func coverage() { lab.page = .coverage }
    @objc private func sideHID() { lab.page = .sideHID }
    @objc private func guide() { if let url = Bundle.main.url(forResource:"使用说明",withExtension:"txt") { NSWorkspace.shared.open(url) } }
}

@main struct MQ80FirmwareLabApp {
    @MainActor static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate(); app.delegate = delegate
        app.run()
        withExtendedLifetime(delegate) {}
    }
}
