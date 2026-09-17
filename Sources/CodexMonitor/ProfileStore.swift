import Foundation

/// One stored login. `main` is `$CODEX_HOME/auth.json` (what Codex CLI / the desktop app
/// actually use). Every other profile lives in `~/.codex-accounts/<name>/auth.json`, a
/// directory that doubles as an isolated `CODEX_HOME` for `codex login`.
struct Profile: Identifiable, Hashable {
    let id: String
    let name: String
    let directory: URL
    let authURL: URL
    let isMain: Bool
    var auth: AuthFile?
    var identity: Identity?
    var loadError: String?
    var modified: Date?
    var fileSize: Int = 0

    var accountId: String? { auth?.tokens?.accountId ?? identity?.accountId }
    var email: String? { identity?.email }
    var accessToken: String? { auth?.tokens?.accessToken }
    var isChatGPTAuth: Bool { auth?.isChatGPTAuth ?? false }
    var lastRefresh: Date? { auth?.lastRefreshDate }

    var displayName: String {
        if let e = email, !e.isEmpty { return e }
        if auth?.openaiApiKey != nil { return "\(name) (API key)" }
        return name
    }

    /// Signature used to detect on-disk changes without re-parsing.
    var signature: String { "\(authURL.path)|\(modified?.timeIntervalSince1970 ?? 0)|\(fileSize)" }

    static func == (lhs: Profile, rhs: Profile) -> Bool { lhs.id == rhs.id && lhs.signature == rhs.signature }
    func hash(into hasher: inout Hasher) { hasher.combine(id) }
}

enum StoreError: LocalizedError {
    case missingAuth(String)
    case invalidName(String)
    case alreadyExists(String)
    case io(String)

    var errorDescription: String? {
        switch self {
        case .missingAuth(let p): return L.errMissingAuth(p)
        case .invalidName(let n): return L.errInvalidName(n)
        case .alreadyExists(let n): return L.errAlreadyExists(n)
        case .io(let m): return m
        }
    }
}

final class ProfileStore {
    static let shared = ProfileStore()

    let codexHome: URL
    let accountsRoot: URL
    let backupDir: URL
    let cacheDir: URL

    init() {
        let env = ProcessInfo.processInfo.environment
        let home = FileManager.default.homeDirectoryForCurrentUser
        if let ch = env["CODEX_HOME"], !ch.isEmpty {
            codexHome = URL(fileURLWithPath: (ch as NSString).expandingTildeInPath)
        } else {
            codexHome = home.appendingPathComponent(".codex")
        }
        if let ar = env["CODEX_ACCOUNTS_DIR"], !ar.isEmpty {
            accountsRoot = URL(fileURLWithPath: (ar as NSString).expandingTildeInPath)
        } else {
            accountsRoot = home.appendingPathComponent(".codex-accounts")
        }
        backupDir = accountsRoot.appendingPathComponent("_backup")
        cacheDir = accountsRoot.appendingPathComponent(".cache")
    }

    var mainAuthURL: URL { codexHome.appendingPathComponent("auth.json") }

    // MARK: Loading

    func loadMain() -> Profile? {
        guard FileManager.default.fileExists(atPath: mainAuthURL.path) else { return nil }
        return load(id: "__main__", name: "~/.codex", directory: codexHome, isMain: true)
    }

    func loadProfiles() -> [Profile] {
        let fm = FileManager.default
        guard let items = try? fm.contentsOfDirectory(at: accountsRoot, includingPropertiesForKeys: [.isDirectoryKey], options: [.skipsHiddenFiles]) else {
            return []
        }
        var out: [Profile] = []
        for dir in items {
            let name = dir.lastPathComponent
            if name.hasPrefix("_") || name.hasPrefix(".") { continue }
            guard (try? dir.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true else { continue }
            guard fm.fileExists(atPath: dir.appendingPathComponent("auth.json").path) else { continue }
            out.append(load(id: name, name: name, directory: dir, isMain: false))
        }
        return out.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }

    static func hasStoredCredentials(in directory: URL) throws -> Bool {
        let url = directory.appendingPathComponent("auth.json")
        let data: Data
        do { data = try Data(contentsOf: url) }
        catch let error as NSError where error.domain == NSCocoaErrorDomain && error.code == NSFileReadNoSuchFileError {
            return false
        }
        guard let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return false }
        let tokens = object["tokens"] as? [String: Any]
        return [object["OPENAI_API_KEY"], tokens?["access_token"], tokens?["refresh_token"]]
            .contains { value in
                guard let text = value as? String else { return false }
                return !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            }
    }

    // Keep aliases on disk; show one representative per workspace and user.
    static func displayProfiles(_ profiles: [Profile]) -> [Profile] {
        var result: [Profile] = []
        var positions: [[String]: Int] = [:]
        for profile in profiles {
            guard let account = profile.accountId, !account.isEmpty else {
                result.append(profile)
                continue
            }
            let person: String
            if let user = profile.identity?.userId, !user.isEmpty {
                person = "user:" + user
            } else if let email = profile.email, !email.isEmpty {
                person = "email:" + email.lowercased()
            } else {
                // Without a user identity, do not merge possibly shared workspaces.
                result.append(profile)
                continue
            }
            let key = [account, person]
            if let index = positions[key] {
                let existing = result[index]
                let expired = profile.identity?.accessTokenExpired == true
                let existingExpired = existing.identity?.accessTokenExpired == true
                let date = profile.lastRefresh ?? profile.modified ?? .distantPast
                let existingDate = existing.lastRefresh ?? existing.modified ?? .distantPast
                if (existingExpired && !expired) || (expired == existingExpired &&
                    (date > existingDate || (date == existingDate && profile.id < existing.id))) {
                    result[index] = profile
                }
            } else {
                positions[key] = result.count
                result.append(profile)
            }
        }
        return result
    }

    private func load(id: String, name: String, directory: URL, isMain: Bool) -> Profile {
        let authURL = directory.appendingPathComponent("auth.json")
        var p = Profile(id: id, name: name, directory: directory, authURL: authURL, isMain: isMain)
        if let attrs = try? FileManager.default.attributesOfItem(atPath: authURL.path) {
            p.modified = attrs[.modificationDate] as? Date
            p.fileSize = (attrs[.size] as? NSNumber)?.intValue ?? 0
        }
        do {
            let auth = try AuthFile.load(from: authURL)
            p.auth = auth
            p.identity = Identity(auth: auth)
        } catch {
            p.loadError = L.errAuthParse(error.localizedDescription)
        }
        return p
    }

    // MARK: Adopt refreshed tokens

    /// Codex rewrites `~/.codex/auth.json` whenever it refreshes tokens. If that file belongs to a
    /// stored profile and is newer, copy it back so the profile never holds a stale refresh token.
    /// Returns a human-readable message when something was copied.
    @discardableResult
    func adopt(main: Profile, into profiles: [Profile]) -> String? {
        guard let mainAccount = main.accountId, main.auth != nil else { return nil }
        guard let target = profiles.first(where: { $0.accountId == mainAccount }) else { return nil }
        let mainRefresh = main.lastRefresh ?? main.modified ?? .distantPast
        let targetRefresh = target.lastRefresh ?? target.modified ?? .distantPast
        guard mainRefresh > targetRefresh else { return nil }
        do {
            let data = try Data(contentsOf: main.authURL)
            if let existing = try? Data(contentsOf: target.authURL), existing == data { return nil }
            try writeAtomically(data, to: target.authURL)
            return L.adopted(target.name)
        } catch {
            return L.adoptFailed(target.name, error.localizedDescription)
        }
    }

    // MARK: Switching

    /// Make `profile` the active login by copying its auth.json over `$CODEX_HOME/auth.json`.
    /// The current main file is first adopted (if it belongs to a profile) or backed up.
    func activate(_ profile: Profile, currentMain: Profile?, profiles: [Profile]) throws -> [String] {
        var messages: [String] = []
        if let main = currentMain, main.auth != nil {
            if let msg = adopt(main: main, into: profiles) { messages.append(msg) }
            if profiles.first(where: { $0.accountId == main.accountId }) == nil {
                let url = try backupMain(main)
                messages.append(L.backedUp(url.path))
            }
        }
        let data = try Data(contentsOf: profile.authURL)
        try writeAtomically(data, to: mainAuthURL)
        messages.append(L.switched(profile.displayName))
        return messages
    }

    private func backupMain(_ main: Profile) throws -> URL {
        try FileManager.default.createDirectory(at: backupDir, withIntermediateDirectories: true)
        let stamp = DateFormatter()
        stamp.dateFormat = "yyyyMMdd-HHmmss"
        let tag = (main.email ?? main.accountId ?? "unknown").replacingOccurrences(of: "@", with: "_at_")
        let url = backupDir.appendingPathComponent("auth-\(tag)-\(stamp.string(from: Date())).json")
        try FileManager.default.copyItem(at: main.authURL, to: url)
        return url
    }

    /// Store the current `~/.codex/auth.json` as a named profile.
    func saveMainAsProfile(named rawName: String) throws -> Profile {
        let name = sanitise(rawName)
        guard !name.isEmpty else { throw StoreError.invalidName(rawName) }
        let dir = accountsRoot.appendingPathComponent(name)
        if FileManager.default.fileExists(atPath: dir.appendingPathComponent("auth.json").path) {
            throw StoreError.alreadyExists(name)
        }
        guard FileManager.default.fileExists(atPath: mainAuthURL.path) else {
            throw StoreError.missingAuth(mainAuthURL.path)
        }
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let data = try Data(contentsOf: mainAuthURL)
        try writeAtomically(data, to: dir.appendingPathComponent("auth.json"))
        return load(id: name, name: name, directory: dir, isMain: false)
    }

    func suggestedProfileName(for profile: Profile) -> String {
        if let email = profile.email, let local = email.split(separator: "@").first {
            return sanitise(String(local))
        }
        return "account-\(profile.accountId?.prefix(8) ?? "new")"
    }

    func sanitise(_ s: String) -> String {
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "._-"))
        let cleaned = s.unicodeScalars.map { allowed.contains($0) ? Character($0) : Character("-") }
        return String(cleaned).trimmingCharacters(in: CharacterSet(charactersIn: "._-"))
    }

    // MARK: Token refresh persistence

    /// Replace the token fields of an auth.json in place, preserving unknown keys.
    func applyRefreshedTokens(_ refreshed: TokenRefreshResponse, to url: URL) throws {
        let data = try Data(contentsOf: url)
        guard var root = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw StoreError.io(L.errNotJSONObject)
        }
        var tokens = root["tokens"] as? [String: Any] ?? [:]
        if let v = refreshed.idToken { tokens["id_token"] = v }
        if let v = refreshed.accessToken { tokens["access_token"] = v }
        if let v = refreshed.refreshToken { tokens["refresh_token"] = v }
        if tokens["account_id"] == nil, let idToken = refreshed.idToken,
           let claims = JWT.claims(idToken),
           let auth = claims["https://api.openai.com/auth"] as? [String: Any],
           let acct = auth["chatgpt_account_id"] as? String {
            tokens["account_id"] = acct
        }
        root["tokens"] = tokens
        root["last_refresh"] = ISO8601.format(Date())
        let out = try JSONSerialization.data(withJSONObject: root, options: [.prettyPrinted, .sortedKeys])
        try writeAtomically(out, to: url)
    }

    // MARK: Files

    func writeAtomically(_ data: Data, to url: URL) throws {
        let fm = FileManager.default
        try fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let tmp = url.deletingLastPathComponent().appendingPathComponent(".\(url.lastPathComponent).tmp-\(UUID().uuidString)")
        try data.write(to: tmp, options: [.atomic])
        try fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: tmp.path)
        if fm.fileExists(atPath: url.path) {
            _ = try fm.replaceItemAt(url, withItemAt: tmp)
        } else {
            try fm.moveItem(at: tmp, to: url)
        }
        try? fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }

    func ensureDirectories() {
        try? FileManager.default.createDirectory(at: accountsRoot, withIntermediateDirectories: true)
        try? FileManager.default.createDirectory(at: cacheDir, withIntermediateDirectories: true)
    }

    // MARK: Config

    /// `chatgpt_base_url` from `$CODEX_HOME/config.toml`, or the default backend.
    func chatGPTBaseURL() -> URL {
        let fallback = URL(string: "https://chatgpt.com/backend-api")!
        let configURL = codexHome.appendingPathComponent("config.toml")
        guard let text = try? String(contentsOf: configURL, encoding: .utf8) else { return fallback }
        for line in text.split(whereSeparator: \.isNewline) {
            let t = line.trimmingCharacters(in: .whitespaces)
            guard t.hasPrefix("chatgpt_base_url") else { continue }
            guard let eq = t.firstIndex(of: "=") else { continue }
            var value = t[t.index(after: eq)...].trimmingCharacters(in: .whitespaces)
            if let hash = value.firstIndex(of: "#") { value = String(value[..<hash]).trimmingCharacters(in: .whitespaces) }
            value = value.trimmingCharacters(in: CharacterSet(charactersIn: "\"'"))
            if let u = URL(string: value), u.scheme != nil { return u }
        }
        return fallback
    }
}
