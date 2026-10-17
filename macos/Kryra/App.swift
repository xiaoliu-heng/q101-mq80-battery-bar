import AppKit
import Combine
import SwiftUI

@MainActor final class ControlAppDelegate: NSObject, NSApplicationDelegate {
    let model: AppModel
    private var status: NSStatusItem!
    private var popover: NSPopover!
    private var window: NSWindow?
    private var subscriptions = Set<AnyCancellable>()
    private var observers: [NSObjectProtocol] = []
    init(demo: Bool) { model = AppModel(demo:demo); super.init() }
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        status = NSStatusBar.system.statusItem(withLength:NSStatusItem.variableLength)
        status.button?.target = self; status.button?.action = #selector(togglePopover)
        status.button?.imagePosition = .imageLeading
        updateStatus()
        popover = NSPopover(); popover.behavior = .semitransient
        popover.contentViewController = FixedPanelHostingController(content:panel)
        popover.contentSize = PanelMetrics.size
        model.showWindow = { [weak self] in self?.showPanel() }
        model.keyboard.objectWillChange.sink { [weak self] in
            DispatchQueue.main.async { self?.updateStatus() }
        }.store(in:&subscriptions)
        let center = NSWorkspace.shared.notificationCenter
        observers.append(center.addObserver(forName:NSWorkspace.willSleepNotification,object:nil,queue:.main) { [weak self] _ in
            Task { @MainActor in await self?.model.keyboard.suspend() }
        })
        observers.append(center.addObserver(forName:NSWorkspace.didWakeNotification,object:nil,queue:.main) { [weak self] _ in
            Task { @MainActor in await self?.model.keyboard.resume() }
        })
        installMenu()
        Task { await model.start() }
        if !UserDefaults.standard.bool(forKey:"hasOpenedControlPanel") || CommandLine.arguments.contains("--show-window") {
            showPanel()
            if !model.demo { UserDefaults.standard.set(true,forKey:"hasOpenedControlPanel") }
        }
    }
    private var panel: some View {
        ControlPanel(app:model,keyboard:model.keyboard)
            .accessibilityHidden(ProcessInfo.processInfo.environment["KRYRA_UI_HIDE_ACCESSIBILITY"] == "1")
    }
    private func updateStatus() {
        let keyboard = model.keyboard
        status.button?.title = keyboard.battery.map { " \($0)%\(keyboard.stale ? "·" : "")" } ?? " —"
        status.button?.font = NSFont.monospacedDigitSystemFont(ofSize:12,weight:.medium)
        let symbol = keyboard.connected ? "keyboard" : "keyboard.badge.ellipsis"
        status.button?.image = NSImage(systemSymbolName:symbol,accessibilityDescription:"Kryra 键盘") ?? NSImage(systemSymbolName:"keyboard",accessibilityDescription:nil)
        status.button?.toolTip = keyboard.stale ? "Kryra：上次电量读数，点击查看连接状态" : "Kryra · \(keyboard.connectionName) · \(keyboard.battery ?? 0)%"
        status.button?.setAccessibilityLabel("Kryra 键盘电量 \(keyboard.battery.map(String.init) ?? "未知")\(keyboard.stale ? "，上次读数" : "")")
    }
    @objc private func togglePopover() {
        if popover.isShown { popover.performClose(nil) }
        else if let button = status.button {
            popover.contentSize = PanelMetrics.size
            NSApp.activate(ignoringOtherApps:true)
            popover.show(relativeTo:button.bounds,of:button,preferredEdge:.minY)
        }
    }
    @objc func showPanel() {
        popover?.performClose(nil)
        if window == nil {
            let panel = NSWindow(contentRect:NSRect(origin:.zero,size:PanelMetrics.size),styleMask:[.titled,.closable,.miniaturizable],backing:.buffered,defer:false)
            panel.title = "Kryra\(model.demo ? " · 演示" : "")"; panel.isReleasedWhenClosed = false
            panel.titleVisibility = .hidden; panel.titlebarAppearsTransparent = true
            panel.isOpaque = false; panel.backgroundColor = .clear
            panel.contentMinSize = PanelMetrics.size; panel.contentMaxSize = PanelMetrics.size
            panel.contentView = fixedPanelView(self.panel); panel.setContentSize(PanelMetrics.size)
            panel.center(); window = panel
        }
        window?.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps:true)
    }
    private func installMenu() {
        let bar = NSMenu(), item = NSMenuItem(), menu = NSMenu(title:AppIdentity.name)
        for (title,action,key) in [("打开控制面板",#selector(showPanel),"o"),("刷新键盘电量",#selector(refresh),"r"),("恢复键盘原灯效",#selector(restoreLighting),""),("退出 Kryra",#selector(NSApplication.terminate(_:)),"q")] {
            let entry = NSMenuItem(title:title,action:action,keyEquivalent:key)
            entry.target = title.hasPrefix("退出") ? NSApp : self; menu.addItem(entry)
        }
        item.submenu = menu; bar.addItem(item)
        let editItem = NSMenuItem(), edit = NSMenu(title:"编辑")
        for (title,action,key) in [("复制","copy:","c"),("粘贴","paste:","v"),("全选","selectAll:","a")] { edit.addItem(NSMenuItem(title:title,action:Selector(action),keyEquivalent:key)) }
        editItem.submenu = edit; bar.addItem(editItem); NSApp.mainMenu = bar
    }
    @objc private func refresh() { Task { _ = try? await model.keyboard.refresh() } }
    @objc private func restoreLighting() { Task { _ = try? await model.keyboard.release() } }
    func applicationShouldHandleReopen(_ sender: NSApplication,hasVisibleWindows flag: Bool) -> Bool { showPanel(); return true }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        Task { await model.shutdown(); NSApp.reply(toApplicationShouldTerminate:true) }
        return .terminateLater
    }
    func applicationWillTerminate(_ notification: Notification) {
        RuntimeFiles.removeAPI()
        for observer in observers { NSWorkspace.shared.notificationCenter.removeObserver(observer) }
    }
}

@main @MainActor struct KryraApplication {
    private static var delegate: ControlAppDelegate?
    private static var signals: [DispatchSourceSignal] = []
    static func main() {
        let demo = CommandLine.arguments.contains("--demo") || ProcessInfo.processInfo.environment["KRYRA_DEMO"] == "1"
        if CommandLine.arguments.contains("--headless") {
            let model = AppModel(demo:demo)
            for number in [SIGTERM,SIGINT] {
                signal(number,SIG_IGN)
                let source = DispatchSource.makeSignalSource(signal:number,queue:.main)
                source.setEventHandler { Task { await model.shutdown(); exit(0) } }
                source.resume(); signals.append(source)
            }
            Task {
                await model.start()
                if let port = model.apiPort { print("{\"ready\":true,\"port\":\(port),\"demo\":\(demo)}"); fflush(stdout) }
                else { fputs("本机 API 启动失败。\n",stderr); exit(1) }
            }
            RunLoop.main.run()
        } else {
            let app = NSApplication.shared
            let delegate = ControlAppDelegate(demo:demo); self.delegate = delegate; app.delegate = delegate
            app.run()
        }
    }
}
