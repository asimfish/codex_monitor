import Foundation
import Network

/// Minimal loopback HTTP listener for the OAuth redirect (`http://localhost:1455/auth/callback`).
/// Answers exactly one callback, renders a small HTML page, and reports the query to the caller.
final class CallbackServer {
    private let listener: NWListener
    private let queue = DispatchQueue(label: "codexmonitor.oauth.callback")
    private var handler: ((Result<[String: String], Error>) -> Void)?
    private let lock = NSLock()

    init(port: UInt16) throws {
        let params = NWParameters.tcp
        params.allowLocalEndpointReuse = true
        params.requiredLocalEndpoint = NWEndpoint.hostPort(host: "127.0.0.1", port: NWEndpoint.Port(rawValue: port)!)
        listener = try NWListener(using: params)
    }

    func start(_ handler: @escaping (Result<[String: String], Error>) -> Void) {
        self.handler = handler
        listener.newConnectionHandler = { [weak self] conn in self?.handle(conn) }
        listener.stateUpdateHandler = { [weak self] state in
            if case .failed(let error) = state {
                if case .posix(let code) = error, code == .EADDRINUSE {
                    self?.finish(.failure(OAuthError.portBusy))
                } else {
                    self?.finish(.failure(OAuthError.listener(error.localizedDescription)))
                }
            }
        }
        listener.start(queue: queue)
    }

    func stop() {
        lock.lock()
        handler = nil
        lock.unlock()
        listener.cancel()
    }

    private func handle(_ conn: NWConnection) {
        conn.start(queue: queue)
        conn.receive(minimumIncompleteLength: 1, maximumLength: 16384) { [weak self] data, _, _, _ in
            guard let self = self else { conn.cancel(); return }
            let text = data.flatMap { String(data: $0, encoding: .utf8) } ?? ""
            let firstLine = text.split(whereSeparator: \.isNewline).first.map(String.init) ?? ""
            guard let req = OAuthCore.parseRequestLine(firstLine) else {
                self.respond(conn, status: "400 Bad Request", html: "<h1>Bad request</h1>")
                return
            }
            guard req.path == OAuthConfig.callbackPath else {
                self.respond(conn, status: "404 Not Found", html: "<h1>Not found</h1>")
                return
            }
            if let err = req.query["error"] {
                self.respond(conn, status: "200 OK", html: L.oauthFailureHTML(err))
                self.finish(.failure(OAuthError.provider(err, req.query["error_description"])))
            } else if req.query["code"] != nil {
                self.respond(conn, status: "200 OK", html: L.oauthSuccessHTML)
                self.finish(.success(req.query))
            } else {
                self.respond(conn, status: "400 Bad Request", html: "<h1>Missing code</h1>")
            }
        }
    }

    private func respond(_ conn: NWConnection, status: String, html: String) {
        let body = Data(html.utf8)
        let head = "HTTP/1.1 \(status)\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: \(body.count)\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n"
        conn.send(content: Data(head.utf8) + body, completion: .contentProcessed { _ in conn.cancel() })
    }

    private func finish(_ result: Result<[String: String], Error>) {
        lock.lock()
        let h = handler
        handler = nil
        lock.unlock()
        h?(result)
    }
}

/// One authorization-code login: owns the PKCE pair, the state nonce and the callback server.
@MainActor
final class BrowserLoginFlow {
    let pkce = PKCE()
    let state = OAuthCore.randomState()
    private var server: CallbackServer?

    var authorizeURL: URL { OAuthCore.authorizeURL(challenge: pkce.challenge, state: state) }

    func start(onCallback: @escaping @MainActor (Result<[String: String], Error>) -> Void) throws {
        let s = try CallbackServer(port: OAuthConfig.port)
        server = s
        s.start { result in
            Task { @MainActor in onCallback(result) }
        }
    }

    func exchange(code: String) async throws -> TokenRefreshResponse {
        var req = URLRequest(url: OAuthConfig.issuer.appendingPathComponent("oauth/token"))
        req.httpMethod = "POST"
        req.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        req.httpBody = OAuthCore.tokenRequestBody(code: code, verifier: pkce.verifier)
        let (data, response) = try await URLSession.shared.data(for: req)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else {
            throw OAuthError.tokenExchange(status, String(data: data.prefix(400), encoding: .utf8) ?? "")
        }
        return try JSONDecoder().decode(TokenRefreshResponse.self, from: data)
    }

    func stop() {
        server?.stop()
        server = nil
    }
}
