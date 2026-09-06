import Foundation

enum UsageError: LocalizedError {
    case noToken
    case unauthorized(String)
    case http(Int, String)
    case network(String)
    case decode(String)

    var errorDescription: String? {
        switch self {
        case .noToken: return L.errNoToken
        case .unauthorized(let m): return L.errUnauthorized(m)
        case .http(let code, let m): return L.errHTTP(code, m)
        case .network(let m): return L.errNetwork(m)
        case .decode(let m): return L.errDecode(m)
        }
    }

    var isAuthFailure: Bool {
        if case .unauthorized = self { return true }
        return false
    }
}

/// Read-only client for the ChatGPT backend endpoints Codex itself uses.
/// Nothing here writes to disk; token refresh is exposed separately and is opt-in.
struct UsageClient {
    static let oauthTokenURL = URL(string: "https://auth.openai.com/oauth/token")!
    static let oauthClientId = "app_EMoamEEZ73f0CkXaXp7hrann"

    let baseURL: URL
    let session: URLSession

    init(baseURL: URL) {
        self.baseURL = baseURL
        let cfg = URLSessionConfiguration.ephemeral
        cfg.timeoutIntervalForRequest = 20
        cfg.timeoutIntervalForResource = 30
        cfg.waitsForConnectivity = false
        cfg.httpAdditionalHeaders = [
            "User-Agent": "codex_cli_rs/0.153.3 (Mac OS; arm64) CodexMonitor/1.0",
            "Accept": "application/json",
            "originator": "codex_cli_rs",
        ]
        session = URLSession(configuration: cfg)
    }

    func fetchUsage(accessToken: String, accountId: String?) async throws -> UsageResponse {
        try await get("wham/usage", accessToken: accessToken, accountId: accountId)
    }

    func fetchResetCredits(accessToken: String, accountId: String?) async throws -> ResetCreditsResponse {
        try await get("wham/rate-limit-reset-credits", accessToken: accessToken, accountId: accountId)
    }

    private func get<T: Decodable>(_ path: String, accessToken: String, accountId: String?) async throws -> T {
        var req = URLRequest(url: baseURL.appendingPathComponent(path))
        req.httpMethod = "GET"
        req.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
        if let acct = accountId, !acct.isEmpty {
            req.setValue(acct, forHTTPHeaderField: "ChatGPT-Account-Id")
        }
        let (data, response) = try await perform(req)
        try Self.check(response, data: data)
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw UsageError.decode("\(error)")
        }
    }

    /// Exchanges a refresh token for a new token set. This rotates the refresh token server-side,
    /// so any other copy of the same auth.json may stop working; callers must warn the user.
    func refreshTokens(refreshToken: String) async throws -> TokenRefreshResponse {
        var req = URLRequest(url: Self.oauthTokenURL)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: String] = [
            "client_id": Self.oauthClientId,
            "grant_type": "refresh_token",
            "refresh_token": refreshToken,
            "scope": "openid profile email",
        ]
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, response) = try await perform(req)
        try Self.check(response, data: data)
        do {
            return try JSONDecoder().decode(TokenRefreshResponse.self, from: data)
        } catch {
            throw UsageError.decode("\(error)")
        }
    }

    private func perform(_ req: URLRequest) async throws -> (Data, URLResponse) {
        do {
            return try await session.data(for: req)
        } catch {
            throw UsageError.network(error.localizedDescription)
        }
    }

    private static func check(_ response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else { return }
        guard (200..<300).contains(http.statusCode) else {
            let snippet = String(data: data.prefix(300), encoding: .utf8) ?? ""
            if http.statusCode == 401 { throw UsageError.unauthorized(snippet) }
            throw UsageError.http(http.statusCode, snippet)
        }
    }
}
