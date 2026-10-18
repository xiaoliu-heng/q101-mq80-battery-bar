import Foundation
import Network

struct HTTPRequest {
    let method: String, target: String, headers: [String:String], body: Data
    var path: String { URLComponents(string:target)?.path ?? "" }
    var query: [URLQueryItem] { URLComponents(string:target)?.queryItems ?? [] }
    static func parse(_ data: Data) throws -> HTTPRequest? {
        guard data.count <= 24576 else { throw ControlError("请求过大。",status:413) }
        guard let separator = data.range(of:Data("\r\n\r\n".utf8)) else {
            if data.count > 8192 { throw ControlError("请求头过大。",status:431) }
            return nil
        }
        guard separator.lowerBound <= 8192, let text = String(data:data[..<separator.lowerBound],encoding:.utf8) else { throw ControlError("无效请求头。",status:400) }
        let lines = text.components(separatedBy:"\r\n")
        let first = lines[0].split(separator:" ",omittingEmptySubsequences:false)
        guard first.count == 3, first[2] == "HTTP/1.1", ["GET","POST"].contains(String(first[0])),
              first[1].hasPrefix("/"), !first[1].hasPrefix("//"), !first[1].contains("#"),
              lines.count <= 41 else { throw ControlError("仅支持 HTTP/1.1 GET / POST。",status:400) }
        var headers: [String:String] = [:]
        for line in lines.dropFirst() {
            guard let colon = line.firstIndex(of:":") else { throw ControlError("无效请求头。",status:400) }
            let name = String(line[..<colon]).lowercased()
            guard !name.isEmpty, name.utf8.allSatisfy({ (97...122).contains($0) || (48...57).contains($0) || $0 == 45 }), headers[name] == nil else { throw ControlError("重复或无效请求头。",status:400) }
            let value = String(line[line.index(after:colon)...]).trimmingCharacters(in:.whitespaces)
            guard !value.contains("\r"), !value.contains("\n") else { throw ControlError("无效请求头。",status:400) }
            headers[name] = value
        }
        guard headers["transfer-encoding"] == nil else { throw ControlError("不支持分块请求。",status:400) }
        let rawLength = headers["content-length"] ?? "0"
        guard !rawLength.isEmpty, rawLength.utf8.allSatisfy({ (48...57).contains($0) }), let length = Int(rawLength), length <= 16384 else { throw ControlError("无效 Content-Length。",status:413) }
        guard first[0] != "GET" || length == 0 else { throw ControlError("GET 不接受请求体。",status:400) }
        let total = separator.upperBound + length
        guard data.count >= total else { return nil }
        guard data.count == total else { throw ControlError("每条连接只接受一个请求。",status:400) }
        return HTTPRequest(method:String(first[0]),target:String(first[1]),headers:headers,body:data[separator.upperBound..<total])
    }
    func authorized(token: String, port: UInt16) -> Bool {
        guard headers["origin"] == nil, headers["host"] == "127.0.0.1:\(port)" else { return false }
        let expected = Array("Bearer \(token)".utf8), supplied = Array((headers["authorization"] ?? "").utf8)
        guard supplied.count == expected.count else { return false }
        var difference: UInt8 = 0
        for i in expected.indices { difference |= expected[i] ^ supplied[i] }
        return difference == 0
    }
    func json() throws -> [String:Any] {
        guard headers["content-type"]?.split(separator:";").first?.trimmingCharacters(in:.whitespaces).lowercased() == "application/json",
              let value = try? JSONSerialization.jsonObject(with:body) as? [String:Any] else { throw ControlError("请求体需要 application/json 对象。",status:400) }
        return value
    }
}

struct HTTPResponse {
    let status: Int
    let value: [String:Any]
    init(_ value: [String:Any], status: Int = 200) { self.value = value; self.status = status }
    static func error(_ error: Error) -> HTTPResponse {
        HTTPResponse(["error":["message":error.localizedDescription]],status:(error as? ControlError)?.status ?? 500)
    }
    var data: Data {
        let body = (try? JSONSerialization.data(withJSONObject:value,options:[.sortedKeys,.fragmentsAllowed])) ?? Data("{}".utf8)
        let header = "HTTP/1.1 \(status) \(status == 200 ? "OK" : "Error")\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: \(body.count)\r\nConnection: close\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n\r\n"
        return Data(header.utf8) + body
    }
}

final class LocalAPIServer: @unchecked Sendable {
    typealias Handler = (HTTPRequest, @escaping (HTTPResponse) -> Void) -> (() -> Void)
    private let queue = DispatchQueue(label:"Kryra local API")
    private let token: String
    private let handler: Handler
    private var listener: NWListener?
    private var connections: [UUID: HTTPConnection] = [:]
    private(set) var port: UInt16 = 0
    init(token: String, handler: @escaping Handler) { self.token = token; self.handler = handler }
    func start() async throws -> UInt16 {
        try await withCheckedThrowingContinuation { continuation in
            queue.async { [self] in
                do {
                    let parameters = NWParameters.tcp
                    parameters.requiredLocalEndpoint = .hostPort(host:"127.0.0.1",port:.any)
                    let listener = try NWListener(using:parameters,on:.any)
                    self.listener = listener
                    var finished = false
                    listener.stateUpdateHandler = { [weak self] state in
                        guard let self, !finished else { return }
                        switch state {
                        case .ready:
                            guard let port = listener.port?.rawValue else { finished = true; continuation.resume(throwing:ControlError("API 端口不可用。")); return }
                            self.port = port; finished = true; continuation.resume(returning:port)
                        case .failed(let error): finished = true; continuation.resume(throwing:error)
                        case .cancelled: finished = true; continuation.resume(throwing:ControlError("API 已停止。"))
                        default: break
                        }
                    }
                    listener.newConnectionHandler = { [weak self] connection in
                        guard let self, self.connections.count < 16 else { connection.cancel(); return }
                        let id = UUID()
                        let session = HTTPConnection(connection,queue:self.queue) { [weak self] request, reply in
                            guard let self else { return {} }
                            guard request.authorized(token:self.token,port:self.port) else { reply(.error(ControlError("本机 API 鉴权失败。",status:401))); return {} }
                            return self.handler(request,reply)
                        } completed: { [weak self] in self?.connections.removeValue(forKey:id) }
                        self.connections[id] = session; session.start()
                    }
                    listener.start(queue:queue)
                } catch { continuation.resume(throwing:error) }
            }
        }
    }
    func stop() {
        queue.async { [self] in
            listener?.stateUpdateHandler = nil; listener?.newConnectionHandler = nil
            listener?.cancel(); listener = nil
            let active = Array(connections.values); connections.removeAll()
            active.forEach { $0.cancel() }
        }
    }
}

private final class HTTPConnection: @unchecked Sendable {
    let connection: NWConnection, queue: DispatchQueue
    let handler: LocalAPIServer.Handler, completed: ()->Void
    var received = Data(), done = false, deadline: DispatchWorkItem?
    var cancelWork: (() -> Void)?
    init(_ connection: NWConnection, queue: DispatchQueue,
         handler: @escaping LocalAPIServer.Handler, completed: @escaping ()->Void) {
        self.connection = connection; self.queue = queue; self.handler = handler; self.completed = completed
    }
    func start() {
        let deadline = DispatchWorkItem { [weak self] in self?.respond(.error(ControlError("请求超时。",status:408))) }
        self.deadline = deadline; queue.asyncAfter(deadline:.now()+5,execute:deadline)
        connection.stateUpdateHandler = { [weak self] state in if case .failed = state { self?.cancel() } }
        connection.start(queue:queue); read()
    }
    func read() {
        connection.receive(minimumIncompleteLength:1,maximumLength:8192) { [weak self] data, _, ended, error in
            guard let self, !done else { return }
            if let data { received.append(data) }
            do {
                if let request = try HTTPRequest.parse(received) {
                    cancelWork = handler(request) { [weak self] response in self?.queue.async { self?.respond(response) } }
                } else if ended || error != nil { cancel() }
                else { read() }
            } catch { respond(.error(error)) }
        }
    }
    func respond(_ response: HTTPResponse) {
        guard !done else { return }; done = true; deadline?.cancel(); cancelWork?(); cancelWork = nil
        connection.send(content:response.data,completion:.contentProcessed { [self] _ in connection.cancel(); completed() })
    }
    func cancel() { guard !done else { return }; done = true; deadline?.cancel(); cancelWork?(); cancelWork = nil; connection.cancel(); completed() }
}
