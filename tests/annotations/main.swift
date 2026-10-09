import Foundation

func check(_ condition: Bool, _ message: String) {
    if !condition { fatalError(message) }
}

let arguments = CommandLine.arguments
if arguments.count > 2 {
    let store = AccountAnnotations(root: URL(fileURLWithPath: arguments[2]))
    switch arguments[1] {
    case "batch":
        for index in 0..<50 { try store.save(key: "profile:swift-\(index)", tags: ["Swift 备注 \(index)"], unavailable: false) }
    case "put":
        try store.save(key: arguments[3], tags: [arguments[4]], unavailable: arguments[5] == "1")
    case "read":
        let value = try store.read()[arguments[3]]!
        print(String(data: try JSONEncoder().encode(value), encoding: .utf8)!)
    default: fatalError("Unknown bridge command")
    }
} else {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent("codex-tag-tests-\(UUID().uuidString)")
    defer { try? FileManager.default.removeItem(at: root) }
    let store = AccountAnnotations(root: root)
    check(try store.read().isEmpty, "Missing file must be empty")
    try store.save(key: "profile:alice", tags: [" 工作 ", "工作", "<b>literal</b>"], unavailable: false)
    try store.save(key: "profile:bob", tags: ["保留其他账号"], unavailable: true)
    var values = try store.read()
    check(values["profile:alice"]?.tags == ["工作", "<b>literal</b>"], "Trim and deduplicate")
    try store.save(key: "profile:alice", tags: ["修改后的标签", "新增"], unavailable: false)
    try store.save(key: "profile:alice", tags: ["修改后的标签"], unavailable: false)
    values = try AccountAnnotations(root: root).read()
    check(values["profile:alice"]?.tags == ["修改后的标签"], "Add/edit/delete must persist")
    check(values["profile:bob"]?.tags == ["保留其他账号"], "Saving another account must preserve existing notes")
    try store.renaming(from: "alice", to: "账号 A", move: {}, rollback: { fatalError("Successful save must not roll back") })
    values = try store.read()
    check(values["profile:alice"] == nil && values["profile:账号 A"]?.tags == ["修改后的标签"], "Rename migrates the old annotation key")
    check(values["profile:bob"]?.tags == ["保留其他账号"], "Rename preserves other annotations")
    let notesBeforeFailure = try Data(contentsOf: store.url)
    var rolledBack = false
    do {
        try store.renaming(from: "账号 A", to: "failed", move: {
            try FileManager.default.removeItem(at: store.url)
            try FileManager.default.createDirectory(at: store.url, withIntermediateDirectories: false)
        }, rollback: {
            rolledBack = true
            try FileManager.default.removeItem(at: store.url)
            try notesBeforeFailure.write(to: store.url)
        })
        fatalError("Metadata save should fail when destination is a directory")
    } catch {}
    let notesAfterRollback = try Data(contentsOf: store.url)
    check(rolledBack && notesAfterRollback == notesBeforeFailure, "Failed metadata write invokes rollback and preserves original labels")
    try store.save(key: "main:acct-original", tags: ["主账号"], unavailable: true)
    values = try store.read()
    check(AccountAnnotations.lookup(values, name: "archived", isMain: false, accountId: "acct-original").tags == ["主账号"], "Archive fallback")
    check(AccountAnnotations.lookup(values, name: "main", isMain: true, accountId: "acct-different").tags.isEmpty, "Changed main identity must not inherit notes")
    for invalid in [[""], [String(repeating: "中", count: 31)], Array(repeating: "x", count: 9), ["x\u{0}y"]] {
        do { _ = try AccountAnnotations.validated(tags: invalid, unavailable: false); fatalError("Invalid tags accepted") }
        catch is AnnotationError {}
    }
    _ = try AccountAnnotations.validated(tags: [String(repeating: "😀", count: 30)], unavailable: false)
    let attributes = try FileManager.default.attributesOfItem(atPath: store.url.path)
    check((attributes[.posixPermissions] as? NSNumber)?.intValue == 0o600, "Metadata must be private")
    let corrupt = Data("{broken".utf8)
    try corrupt.write(to: store.url)
    do { try store.save(key: "profile:alice", tags: ["overwrite"], unavailable: false); fatalError("Corrupt data overwritten") }
    catch is AnnotationError {}
    check(try Data(contentsOf: store.url) == corrupt, "Corrupt metadata must be preserved")
    print("Swift annotation tests passed")
}
