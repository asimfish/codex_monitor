import Darwin
import Foundation

struct AccountAnnotation: Codable, Equatable {
    var tags: [String] = []
    var unavailable = false
}

enum AnnotationError: LocalizedError {
    case invalidTags, invalidFile, accountChanged
    var errorDescription: String? {
        switch self {
        case .invalidTags: return L.tagLimits
        case .invalidFile: return L.invalidTagsFile
        case .accountChanged: return L.tagAccountChanged
        }
    }
}

final class AccountAnnotations {
    private struct Document: Codable {
        var version: Int
        var accounts: [String: AccountAnnotation]
    }

    let root: URL
    var url: URL { root.appendingPathComponent("_annotations.json") }
    init(root: URL) { self.root = root }

    static func key(name: String, isMain: Bool, accountId: String?) -> String {
        isMain ? "main:" + (accountId ?? "") : "profile:" + name
    }

    static func lookup(_ records: [String: AccountAnnotation], name: String, isMain: Bool, accountId: String?) -> AccountAnnotation {
        records[key(name: name, isMain: isMain, accountId: accountId)]
            ?? records["main:" + (accountId ?? "")] ?? AccountAnnotation()
    }

    static func validated(tags: [String], unavailable: Bool) throws -> AccountAnnotation {
        guard tags.count <= 8 else { throw AnnotationError.invalidTags }
        var clean: [String] = []
        for raw in tags {
            let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            guard (1...30).contains(text.unicodeScalars.count),
                  !text.unicodeScalars.contains(where: { $0.properties.generalCategory == .control }) else {
                throw AnnotationError.invalidTags
            }
            if !clean.contains(text) { clean.append(text) }
        }
        return AccountAnnotation(tags: clean, unavailable: unavailable)
    }

    func read() throws -> [String: AccountAnnotation] {
        let data: Data
        do { data = try Data(contentsOf: url) }
        catch let error as NSError where error.domain == NSCocoaErrorDomain && error.code == NSFileReadNoSuchFileError { return [:] }
        guard let document = try? JSONDecoder().decode(Document.self, from: data), document.version == 1 else {
            throw AnnotationError.invalidFile
        }
        for value in document.accounts.values { _ = try Self.validated(tags: value.tags, unavailable: value.unavailable) }
        return document.accounts
    }

    @discardableResult
    func save(key: String, tags: [String], unavailable: Bool) throws -> AccountAnnotation {
        let value = try Self.validated(tags: tags, unavailable: unavailable)
        let fm = FileManager.default
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
        // Python and Swift use the same advisory lock, so different-account edits cannot be lost.
        let fd = open(root.appendingPathComponent("_annotations.lock").path, O_CREAT | O_RDWR, S_IRUSR | S_IWUSR)
        guard fd >= 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
        defer { close(fd) }
        guard flock(fd, LOCK_EX) == 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
        defer { flock(fd, LOCK_UN) }
        var records = try read()
        records[key] = value
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        let payload = try encoder.encode(Document(version: 1, accounts: records))
        let temporary = root.appendingPathComponent("._annotations-\(UUID().uuidString).tmp")
        defer { try? fm.removeItem(at: temporary) }
        guard fm.createFile(atPath: temporary.path, contents: payload, attributes: [.posixPermissions: 0o600]) else {
            throw CocoaError(.fileWriteUnknown)
        }
        guard rename(temporary.path, url.path) == 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
        return value
    }
}
