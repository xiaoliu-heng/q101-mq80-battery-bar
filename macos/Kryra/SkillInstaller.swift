import Foundation

enum SkillInstaller {
    static var defaultRoot: URL { FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".agents/skills",isDirectory:true) }
    static func install(resource: URL, root: URL = defaultRoot) throws -> URL {
        let fm = FileManager.default, target = root.appendingPathComponent("kryra",isDirectory:true)
        let marker = ".kryra.json", owner = "local.xiaoliu.kryra"
        guard fm.fileExists(atPath:resource.appendingPathComponent("SKILL.md").path),
              fm.isExecutableFile(atPath:resource.appendingPathComponent("scripts/kryractl").path) else { throw ControlError("安装资源不完整，请重新下载应用。") }
        if fm.fileExists(atPath:target.path) {
            let data = try? Data(contentsOf:target.appendingPathComponent(marker))
            let value = data.flatMap { try? JSONSerialization.jsonObject(with:$0) as? [String:Any] }
            guard value?["owner"] as? String == owner else { throw ControlError("该目录已有其他来源的 skill，请选择其他安装目录：\(target.path)") }
        }
        try fm.createDirectory(at:root,withIntermediateDirectories:true)
        let stage = root.appendingPathComponent(".kryra-\(UUID().uuidString)",isDirectory:true)
        let backup = root.appendingPathComponent(".kryra-backup-\(UUID().uuidString)",isDirectory:true)
        defer { try? fm.removeItem(at:stage) }
        try fm.copyItem(at:resource,to:stage)
        let identity = try JSONSerialization.data(withJSONObject:["owner":owner,"app_version":AppIdentity.version])
        try identity.write(to:stage.appendingPathComponent(marker))
        let existed = fm.fileExists(atPath:target.path)
        if existed { try fm.moveItem(at:target,to:backup) }
        do { try fm.moveItem(at:stage,to:target) }
        catch { if existed { try? fm.moveItem(at:backup,to:target) }; throw error }
        if existed { try? fm.removeItem(at:backup) }
        return target
    }
}
