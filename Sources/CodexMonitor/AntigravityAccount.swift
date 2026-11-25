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

    var hasToken: Bool {
        let file = directory.appendingPathComponent("antigravity-cli/antigravity-oauth-token")
        let values = try? file.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
        return values?.isRegularFile == true && values?.isSymbolicLink == false
    }

}

struct AntigravityAccountStatus {
    var groups: [AntigravityQuota.Group] = []
    var sync: Date?
    var error: String?
    var eligibility: AntigravityCLI.Eligibility?
}
