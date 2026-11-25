import Foundation

struct AntigravityAccount: Codable, Identifiable {
    let id: UUID
    let name: String

    private static let defaultsKey = "antigravitySavedAccounts"

    static func load() -> [Self] {
        guard let data = UserDefaults.standard.data(forKey: defaultsKey) else { return [] }
        return (try? JSONDecoder().decode([Self].self, from: data)) ?? []
    }

    static func save(_ accounts: [Self]) {
        if let data = try? JSONEncoder().encode(accounts) {
            UserDefaults.standard.set(data, forKey: defaultsKey)
        }
    }

    var directory: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/CodexMonitor/Antigravity", isDirectory: true)
            .appendingPathComponent(id.uuidString, isDirectory: true)
    }

    var loginFinished: URL { directory.appendingPathComponent("login-finished") }

    var hasToken: Bool {
        let file = directory.appendingPathComponent("antigravity-cli/antigravity-oauth-token")
        let values = try? file.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
        return values?.isRegularFile == true && values?.isSymbolicLink == false
    }

    func prepareLogin(command: URL) throws -> URL {
        let fm = FileManager.default
        try fm.createDirectory(at: directory, withIntermediateDirectories: true,
                               attributes: [.posixPermissions: 0o700])
        try fm.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)
        if fm.fileExists(atPath: loginFinished.path) {
            try fm.removeItem(at: loginFinished)
        }
        let script = directory.appendingPathComponent("login.command")
        let work = FileManager.default.homeDirectoryForCurrentUser.path
        let body = """
        #!/bin/sh
        umask 077
        finished=\(shellQuote(loginFinished.path))
        trap ': > "$finished"' EXIT
        cd \(shellQuote(directory.path)) || exit 1
        /usr/bin/env -i HOME=\(shellQuote(work)) PATH=/usr/bin:/bin:/usr/sbin:/sbin TERM=xterm-256color \\
          AGY_CLI_FORCE_FILE_TOKEN_STORAGE=1 \(shellQuote(command.path)) acp --login \\
          --gemini-dir \(shellQuote(directory.path)) --app-data-dir antigravity-cli
        """
        try Data(body.utf8).write(to: script, options: .atomic)
        try fm.setAttributes([.posixPermissions: 0o700], ofItemAtPath: script.path)
        return script
    }

    // Only program-controlled paths go into the Terminal launcher, never the account name.
    private func shellQuote(_ value: String) -> String {
        "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }
}

struct AntigravityAccountStatus {
    var groups: [AntigravityQuota.Group] = []
    var sync: Date?
    var error: String?
}
