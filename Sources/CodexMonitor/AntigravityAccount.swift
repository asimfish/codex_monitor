import Darwin
import Foundation

struct AntigravityAccount: Codable, Identifiable {
    let id: UUID
    let name: String
    var annotation: AccountAnnotation? = nil

    private static let defaultsKey = "antigravitySavedAccounts"
    static let tokenName = "antigravity-oauth-token"
    private static let tokenLimit = 64 * 1024
    private static var root: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/CodexMonitor/Antigravity", isDirectory: true)
    }

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
        Self.root.appendingPathComponent(id.uuidString, isDirectory: true)
    }

    var tokenURL: URL { directory.appendingPathComponent("antigravity-cli/\(Self.tokenName)") }

    var hasToken: Bool {
        (try? withToken { _, _ in true }) ?? false
    }

    var tokenSignature: String? {
        try? withToken { _, fd in
            var info = stat()
            guard fstat(fd, &info) == 0 else { throw Self.fileError() }
            return "\(info.st_mtimespec.tv_sec).\(info.st_mtimespec.tv_nsec):\(info.st_size):\(info.st_ino)"
        }
    }

    func readCredential() throws -> Data {
        try withToken { _, fd in
            let file = FileHandle(fileDescriptor: fd, closeOnDealloc: false)
            guard let data = try file.read(upToCount: Self.tokenLimit + 1),
                  !data.isEmpty, data.count <= Self.tokenLimit else { throw CocoaError(.fileReadCorruptFile) }
            return data
        }
    }

    func signOut() throws {
        try withToken { directory, _ in
            guard unlinkat(directory, Self.tokenName, 0) == 0 else { throw Self.fileError() }
        }
    }

    func archive() throws -> URL? {
        // Check the root and profile before moving anything. An unsigned row may have no files yet.
        let root = open(Self.root.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        if root < 0 && errno == ENOENT { return nil }
        guard root >= 0 else { throw Self.fileError() }
        defer { close(root) }
        let profile = openat(root, id.uuidString, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        if profile < 0 && errno == ENOENT { return nil }
        guard profile >= 0 else { throw Self.fileError() }
        defer { close(profile) }
        if mkdirat(root, "_deleted", 0o700) != 0 && errno != EEXIST { throw Self.fileError() }
        let archive = openat(root, "_deleted", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard archive >= 0 else { throw Self.fileError() }
        defer { close(archive) }
        let name = id.uuidString + "-" + UUID().uuidString
        guard renameat(root, id.uuidString, archive, name) == 0 else { throw Self.fileError() }
        return Self.root.appendingPathComponent("_deleted/\(name)", isDirectory: true)
    }

    func exportCredential(to url: URL) throws {
        let data = try readCredential()
        let parent = url.deletingLastPathComponent()
        var template = parent.appendingPathComponent(".antigravity-export-XXXXXX").path.utf8CString
        let fd = template.withUnsafeMutableBufferPointer { mkstemp($0.baseAddress!) }
        guard fd >= 0 else { throw Self.fileError() }
        let path = template.withUnsafeBufferPointer { String(cString: $0.baseAddress!) }
        let file = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        defer { try? file.close(); unlink(path) }
        try file.write(contentsOf: data)
        try file.synchronize()
        guard rename(path, url.path) == 0 else { throw Self.fileError() }
    }

    private func withToken<T>(_ operation: (Int32, Int32) throws -> T) throws -> T {
        let flags = O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC
        let root = open(Self.root.path, flags)
        guard root >= 0 else { throw Self.fileError() }
        defer { close(root) }
        let profile = openat(root, id.uuidString, flags)
        guard profile >= 0 else { throw Self.fileError() }
        defer { close(profile) }
        let directory = openat(profile, "antigravity-cli", flags)
        guard directory >= 0 else { throw Self.fileError() }
        defer { close(directory) }
        let token = openat(directory, Self.tokenName, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
        guard token >= 0 else { throw Self.fileError() }
        defer { close(token) }
        var info = stat()
        guard fstat(token, &info) == 0, info.st_mode & S_IFMT == S_IFREG, info.st_uid == geteuid(),
              info.st_nlink == 1, (1...Int64(Self.tokenLimit)).contains(info.st_size) else {
            throw CocoaError(.fileReadCorruptFile)
        }
        return try operation(directory, token)
    }

    private static func fileError() -> POSIXError { POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
}

struct AntigravityAccountStatus {
    var groups: [AntigravityQuota.Group] = []
    var sync: Date?
    var error: String?
    var eligibility: AntigravityCLI.Eligibility?
    var email: String?
    var planTier: String?
    var remainingCredits: Int?
    var isStale = false
    var tokenSignature: String?
    var identitySync: Date?
}
