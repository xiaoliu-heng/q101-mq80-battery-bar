import Foundation
import Security
import Darwin

enum AppIdentity {
    static let version = "1.3.0"
    static let bundleID = "local.xiaoliu.kryra"
    static let name = "Kryra"
}

enum RuntimeFiles {
    static var directory: URL {
        if let path = ProcessInfo.processInfo.environment["KRYRA_HOME"], !path.isEmpty { return URL(fileURLWithPath:path,isDirectory:true) }
        return FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Kryra",isDirectory:true)
    }
    static var apiURL: URL { directory.appendingPathComponent("api.json") }
    static func prepare() throws {
        try FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])
        try FileManager.default.setAttributes([.posixPermissions:0o700],ofItemAtPath:directory.path)
    }
    static func token() throws -> String {
        var bytes = [UInt8](repeating:0,count:32)
        guard SecRandomCopyBytes(kSecRandomDefault,bytes.count,&bytes) == errSecSuccess else { throw ControlError("无法生成本机 API 密钥。") }
        return bytes.map { String(format:"%02x",$0) }.joined()
    }
    static func write(_ value: [String:Any], to url: URL) throws {
        try prepare()
        let data = try JSONSerialization.data(withJSONObject:value,options:[.prettyPrinted,.sortedKeys])
        let temporary = directory.appendingPathComponent(".\(UUID().uuidString).tmp")
        let fd = Darwin.open(temporary.path,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW,0o600)
        guard fd >= 0 else { throw ControlError("无法写入应用配置。") }
        var renamed = false
        defer { Darwin.close(fd); if !renamed { try? FileManager.default.removeItem(at:temporary) } }
        try data.withUnsafeBytes { buffer in
            var offset = 0
            while offset < data.count {
                let n = Darwin.write(fd,buffer.baseAddress!.advanced(by:offset),data.count-offset)
                if n < 0 && errno == EINTR { continue }
                guard n > 0 else { throw ControlError("写入应用配置失败。") }
                offset += n
            }
        }
        guard Darwin.rename(temporary.path,url.path) == 0 else { throw ControlError("保存应用配置失败。") }
        renamed = true
    }
    static func readAPI() throws -> [String:Any] {
        var info = stat()
        guard lstat(apiURL.path,&info) == 0, info.st_mode & S_IFMT == S_IFREG,
              info.st_uid == getuid(), info.st_mode & 0o077 == 0 else {
            throw ControlError("本机 API 尚未就绪，请先打开 Kryra。")
        }
        guard let value = try JSONSerialization.jsonObject(with:Data(contentsOf:apiURL)) as? [String:Any],
              value["host"] as? String == "127.0.0.1", let port = value["port"] as? Int,
              (1...65535).contains(port), let token = value["token"] as? String,
              token.count == 64, token.allSatisfy({ $0.isHexDigit }) else { throw ControlError("本机 API 配置无效，请重新打开应用。") }
        return value
    }
    static func removeAPI() {
        if let data = try? Data(contentsOf:apiURL), let value = try? JSONSerialization.jsonObject(with:data) as? [String:Any],
           value["pid"] as? Int == Int(getpid()) { try? FileManager.default.removeItem(at:apiURL) }
    }
}

final class InstanceLock {
    private var descriptor: Int32 = -1
    init() throws {
        try RuntimeFiles.prepare()
        let opened = Darwin.open(RuntimeFiles.directory.appendingPathComponent("instance.lock").path,O_CREAT|O_RDWR|O_NOFOLLOW,0o600)
        guard opened >= 0 else { throw ControlError("无法创建应用运行锁。") }
        guard flock(opened,LOCK_EX|LOCK_NB) == 0 else { Darwin.close(opened); throw ControlError("Kryra 已经在运行。") }
        descriptor = opened
    }
    deinit { if descriptor >= 0 { flock(descriptor,LOCK_UN); Darwin.close(descriptor) } }
}
