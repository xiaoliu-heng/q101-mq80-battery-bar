import AppKit
import Combine
import ServiceManagement

@MainActor final class AppModel: ObservableObject {
    let keyboard: KeyboardController
    @Published private(set) var apiPort: UInt16?
    @Published private(set) var apiIssue: String?
    @Published private(set) var installedSkill: URL?
    @Published private(set) var message: String?
    @Published var skillRoot = SkillInstaller.defaultRoot
    @Published private(set) var loginEnabled = SMAppService.mainApp.status == .enabled
    var showWindow: (() -> Void)?
    private var server: LocalAPIServer?
    private var instanceLock: InstanceLock?
    let demo: Bool
    init(demo: Bool) {
        self.demo = demo
        let defaults = demo ? UserDefaults(suiteName:AppIdentity.bundleID+".demo")! : UserDefaults.standard
        keyboard = KeyboardController(worker:HIDWorker(factory: {
            if demo { return DemoBackend() }
            return IOKitBackend()
        }),demo:demo,defaults:defaults)
    }
    func start() async {
        do { instanceLock = try InstanceLock() }
        catch { apiIssue = error.localizedDescription; return }
        do {
            let token = try RuntimeFiles.token()
            let server = LocalAPIServer(token:token) { [weak self] request, reply in
                let task = Task { @MainActor in
                    guard let self else { reply(.error(ControlError("应用已退出。"))); return }
                    reply(await self.keyboard.handle(request))
                }
                return { task.cancel() }
            }
            self.server = server
            let port = try await server.start()
            try RuntimeFiles.write(["schema_version":1,"host":"127.0.0.1","port":Int(port),"token":token,
                                   "pid":Int(getpid()),"app_version":AppIdentity.version,"demo":demo],to:RuntimeFiles.apiURL)
            try RuntimeFiles.write(["app_path":Bundle.main.bundleURL.path],to:RuntimeFiles.directory.appendingPathComponent("installation.json"))
            apiPort = port
        } catch { apiIssue = error.localizedDescription; server?.stop() }
        keyboard.start()
    }
    func installSkill() {
        do {
            guard let resource = Bundle.main.resourceURL?.appendingPathComponent("Skill/kryra") else { throw ControlError("Skill 资源缺失。") }
            installedSkill = try SkillInstaller.install(resource:resource,root:skillRoot)
            message = "已安装。可在 Codex 中使用 $kryra；若未出现，请重新打开 Codex。"
        } catch { message = error.localizedDescription }
    }
    func chooseSkillRoot() {
        let panel = NSOpenPanel(); panel.title = "选择 skill 父目录（通常为 ~/.agents/skills）"
        panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url { skillRoot = url }
    }
    func setLogin(_ enabled: Bool) {
        do {
            if enabled { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
            loginEnabled = SMAppService.mainApp.status == .enabled
            if enabled && !loginEnabled { message = "请在系统设置 → 通用 → 登录项中允许 Kryra。" }
        } catch { message = "登录项设置失败：\(error.localizedDescription)" }
    }
    func copyCommand() {
        guard let cli = Bundle.main.resourceURL?.appendingPathComponent("bin/kryractl") else { return }
        let quoted = "'" + cli.path.replacingOccurrences(of:"'",with:"'\\''") + "' battery"
        NSPasteboard.general.clearContents(); NSPasteboard.general.setString(quoted,forType:.string)
        message = "电量查询命令已复制。"
    }
    func openGuide() {
        if let url = Bundle.main.resourceURL?.appendingPathComponent("API.html") { NSWorkspace.shared.open(url) }
    }
    func shutdown() async { await keyboard.shutdown(); server?.stop(); RuntimeFiles.removeAPI(); instanceLock = nil }
}
