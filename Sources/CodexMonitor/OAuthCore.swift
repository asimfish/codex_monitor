import CryptoKit
import Foundation

/// The same OAuth client Codex CLI uses for `codex login` (authorization code + PKCE, loopback
/// redirect on port 1455). Reproducing it lets the app control which browser window opens.
enum OAuthConfig {
    static let issuer = URL(string: "https://auth.openai.com")!
    static let clientId = "app_EMoamEEZ73f0CkXaXp7hrann"
    static let port: UInt16 = 1455
    static let redirectURI = "http://localhost:1455/auth/callback"
    static let callbackPath = "/auth/callback"
    static let scope = "openid profile email offline_access api.connectors.read api.connectors.invoke"
}

enum OAuthError: LocalizedError {
    case portBusy
    case listener(String)
    case stateMismatch
    case provider(String, String?)
    case tokenExchange(Int, String)
    case missingTokens

    var errorDescription: String? {
        switch self {
        case .portBusy: return L.oauthPortBusy
        case .listener(let m): return L.oauthListenerFailed(m)
        case .stateMismatch: return L.oauthStateMismatch
        case .provider(let code, let desc): return L.oauthProviderError(code, desc)
        case .tokenExchange(let status, let body): return L.oauthTokenExchangeFailed(status, body)
        case .missingTokens: return L.oauthMissingTokens
        }
    }
}

extension Data {
    func base64URLEncodedString() -> String {
        base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}

struct PKCE {
    let verifier: String
    let challenge: String

    init() {
        let bytes = (0..<64).map { _ in UInt8.random(in: .min ... .max) }
        verifier = Data(bytes).base64URLEncodedString()
        challenge = Data(SHA256.hash(data: Data(verifier.utf8))).base64URLEncodedString()
    }
}

enum OAuthCore {
    static func randomState() -> String {
        Data((0..<32).map { _ in UInt8.random(in: .min ... .max) }).base64URLEncodedString()
    }

    /// Mirrors the query Codex CLI 0.153 builds, so the server treats the login identically.
    static func authorizeURL(challenge: String, state: String) -> URL {
        var comps = URLComponents(url: OAuthConfig.issuer.appendingPathComponent("oauth/authorize"), resolvingAgainstBaseURL: false)!
        comps.queryItems = [
            URLQueryItem(name: "response_type", value: "code"),
            URLQueryItem(name: "client_id", value: OAuthConfig.clientId),
            URLQueryItem(name: "redirect_uri", value: OAuthConfig.redirectURI),
            URLQueryItem(name: "scope", value: OAuthConfig.scope),
            URLQueryItem(name: "code_challenge", value: challenge),
            URLQueryItem(name: "code_challenge_method", value: "S256"),
            URLQueryItem(name: "id_token_add_organizations", value: "true"),
            URLQueryItem(name: "codex_cli_simplified_flow", value: "true"),
            URLQueryItem(name: "state", value: state),
            URLQueryItem(name: "originator", value: "codex_cli_rs"),
        ]
        return comps.url!
    }

    struct Request {
        let method: String
        let path: String
        let query: [String: String]
    }

    /// Parses the first line of an HTTP request, e.g. `GET /auth/callback?code=x&state=y HTTP/1.1`.
    static func parseRequestLine(_ line: String) -> Request? {
        let parts = line.split(separator: " ")
        guard parts.count >= 2 else { return nil }
        let target = String(parts[1])
        var comps = URLComponents()
        if let qIdx = target.firstIndex(of: "?") {
            comps.path = String(target[..<qIdx])
            comps.percentEncodedQuery = String(target[target.index(after: qIdx)...])
        } else {
            comps.path = target
        }
        var query: [String: String] = [:]
        for item in comps.queryItems ?? [] {
            query[item.name] = item.value ?? ""
        }
        return Request(method: String(parts[0]), path: comps.path, query: query)
    }

    private static let formAllowed: CharacterSet = {
        var s = CharacterSet.alphanumerics
        s.insert(charactersIn: "-._~")
        return s
    }()

    static func formEncode(_ fields: [(String, String)]) -> Data {
        let body = fields.map { k, v in
            let ek = k.addingPercentEncoding(withAllowedCharacters: formAllowed) ?? k
            let ev = v.addingPercentEncoding(withAllowedCharacters: formAllowed) ?? v
            return "\(ek)=\(ev)"
        }.joined(separator: "&")
        return Data(body.utf8)
    }

    static func tokenRequestBody(code: String, verifier: String) -> Data {
        formEncode([
            ("grant_type", "authorization_code"),
            ("code", code),
            ("redirect_uri", OAuthConfig.redirectURI),
            ("client_id", OAuthConfig.clientId),
            ("code_verifier", verifier),
        ])
    }

    /// Builds an auth.json in the exact shape Codex CLI writes after a ChatGPT login.
    static func buildAuthJSON(_ tokens: TokenRefreshResponse, now: Date = Date()) throws -> Data {
        guard let idToken = tokens.idToken, let accessToken = tokens.accessToken else { throw OAuthError.missingTokens }
        var accountId: String?
        if let claims = JWT.claims(idToken), let auth = claims["https://api.openai.com/auth"] as? [String: Any] {
            accountId = auth["chatgpt_account_id"] as? String
        }
        if accountId == nil, let claims = JWT.claims(accessToken), let auth = claims["https://api.openai.com/auth"] as? [String: Any] {
            accountId = auth["chatgpt_account_id"] as? String
        }
        var tokenDict: [String: Any] = [
            "id_token": idToken,
            "access_token": accessToken,
        ]
        if let rt = tokens.refreshToken { tokenDict["refresh_token"] = rt }
        if let acct = accountId { tokenDict["account_id"] = acct }
        let root: [String: Any] = [
            "OPENAI_API_KEY": NSNull(),
            "auth_mode": "chatgpt",
            "tokens": tokenDict,
            "last_refresh": ISO8601.format(now),
        ]
        return try JSONSerialization.data(withJSONObject: root, options: [.prettyPrinted, .sortedKeys])
    }
}
