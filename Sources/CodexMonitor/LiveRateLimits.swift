import Foundation
import SQLite3

/// The Codex desktop app (and CLI threads migrated to SQLite history) do not append rollout
/// files any more, but the app-server logs one row per `account/rateLimits/updated` event into
/// `$CODEX_HOME/logs_*.sqlite`. The row has no numbers, but it says *when* usage just changed,
/// so the usage API can be fetched immediately instead of at the next tick.
final class AppServerEventWatcher {
    static let eventPrefix = "app-server event: account/rateLimits/updated"

    let codexHome: URL
    private var db: OpaquePointer?
    private var lastId: Int64 = 0
    private var lastOpenAttempt: Date = .distantPast

    init(codexHome: URL) {
        self.codexHome = codexHome
    }

    deinit {
        if let db = db { sqlite3_close_v2(db) }
    }

    private func findDatabase() -> URL? {
        guard let items = try? FileManager.default.contentsOfDirectory(at: codexHome, includingPropertiesForKeys: [.contentModificationDateKey], options: [.skipsHiddenFiles]) else { return nil }
        let logs = items.filter { $0.lastPathComponent.hasPrefix("logs_") && $0.pathExtension == "sqlite" }
        return logs.max { a, b in
            let ma = (try? a.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
            let mb = (try? b.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
            return ma < mb
        }
    }

    private func open() -> Bool {
        if db != nil { return true }
        guard Date().timeIntervalSince(lastOpenAttempt) > 30 else { return false }
        lastOpenAttempt = Date()
        guard let url = findDatabase() else { return false }
        var handle: OpaquePointer?
        let flags = SQLITE_OPEN_READONLY | SQLITE_OPEN_NOMUTEX
        guard sqlite3_open_v2(url.path, &handle, flags, nil) == SQLITE_OK, let h = handle else {
            if let h = handle { sqlite3_close_v2(h) }
            return false
        }
        sqlite3_busy_timeout(h, 300)
        db = h
        lastId = maxId() ?? 0
        return true
    }

    private func maxId() -> Int64? {
        guard let db = db else { return nil }
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, "SELECT max(id) FROM logs", -1, &stmt, nil) == SQLITE_OK, let s = stmt else { return nil }
        defer { sqlite3_finalize(s) }
        guard sqlite3_step(s) == SQLITE_ROW else { return nil }
        return sqlite3_column_int64(s, 0)
    }

    /// Number of new rateLimits/updated rows since the previous poll.
    func poll() -> Int {
        guard open(), let db = db else { return 0 }
        var stmt: OpaquePointer?
        let sql = "SELECT count(*) FROM logs WHERE id > ? AND target = 'codex_app_server::outgoing_message' AND feedback_log_body LIKE ?"
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK, let s = stmt else {
            reset()
            return 0
        }
        defer { sqlite3_finalize(s) }
        sqlite3_bind_int64(s, 1, lastId)
        let pattern = Self.eventPrefix + "%"
        sqlite3_bind_text(s, 2, pattern, -1, unsafeBitCast(-1, to: sqlite3_destructor_type.self))
        let rc = sqlite3_step(s)
        guard rc == SQLITE_ROW else {
            if rc != SQLITE_BUSY { reset() }
            return 0
        }
        let count = Int(sqlite3_column_int64(s, 0))
        if let m = maxId() { lastId = m }
        return count
    }

    private func reset() {
        if let db = db { sqlite3_close_v2(db) }
        db = nil
    }
}

/// Rate-limit snapshot taken from a `token_count` event that Codex appends to the session
/// rollout (`$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl`) after every model response.
/// This is the same data the Codex app/TUI display, so it is real-time and exact.
struct LiveRateLimitEvent: Equatable {
    var timestamp: Date
    var limitId: String?
    var limitName: String?
    var primary: RateLimitWindow?
    var secondary: RateLimitWindow?
    var planType: String?
    var limitReached: Bool

    /// The account-wide Codex limit (as opposed to per-model limits such as Spark).
    var isMainLimit: Bool { limitId == nil || limitId == "codex" }

    var asRateLimit: RateLimit {
        RateLimit(allowed: limitReached ? false : nil, limitReached: limitReached, primaryWindow: primary, secondaryWindow: secondary)
    }

    static func == (lhs: LiveRateLimitEvent, rhs: LiveRateLimitEvent) -> Bool {
        lhs.timestamp == rhs.timestamp && lhs.limitId == rhs.limitId
            && lhs.primary?.usedPercent == rhs.primary?.usedPercent
            && lhs.secondary?.usedPercent == rhs.secondary?.usedPercent
    }
}

enum RolloutParser {
    /// Cheap pre-filter first; only lines that can be token_count events get JSON-parsed.
    static func parse(line: String) -> LiveRateLimitEvent? {
        guard line.contains("\"token_count\""), line.contains("\"rate_limits\"") else { return nil }
        guard let data = line.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let payload = obj["payload"] as? [String: Any],
              payload["type"] as? String == "token_count",
              let rl = payload["rate_limits"] as? [String: Any],
              let tsRaw = obj["timestamp"] as? String,
              let ts = ISO8601.parse(tsRaw) else { return nil }

        func window(_ any: Any?) -> RateLimitWindow? {
            guard let w = any as? [String: Any], let used = (w["used_percent"] as? NSNumber)?.doubleValue else { return nil }
            let minutes = (w["window_minutes"] as? NSNumber)?.intValue
            let resets = (w["resets_at"] as? NSNumber)?.doubleValue
            return RateLimitWindow(
                usedPercent: used,
                limitWindowSeconds: minutes.map { $0 * 60 },
                resetAfterSeconds: resets.map { max(0, Int($0 - ts.timeIntervalSince1970)) },
                resetAt: resets
            )
        }

        let primary = window(rl["primary"])
        let secondary = window(rl["secondary"])
        guard primary != nil || secondary != nil else { return nil }
        return LiveRateLimitEvent(
            timestamp: ts,
            limitId: rl["limit_id"] as? String,
            limitName: rl["limit_name"] as? String,
            primary: primary,
            secondary: secondary,
            planType: rl["plan_type"] as? String,
            limitReached: (rl["rate_limit_reached_type"] as? String) != nil
        )
    }
}

/// Incrementally reads new bytes appended to recently-modified rollout files under one
/// `sessions` directory. Cheap enough to run every couple of seconds.
final class RolloutTailer {
    let sessionsDir: URL
    /// Only files modified within this window are looked at.
    var recentWindow: TimeInterval = 15 * 60
    /// When a file is seen for the first time, start this far from its end rather than at 0.
    var initialTailBytes: UInt64 = 512 * 1024

    /// A full walk of every day directory happens at most this often; between walks only the
    /// known candidates and today's directory are re-checked.
    var fullScanInterval: TimeInterval = 10

    private var offsets: [String: UInt64] = [:]
    private var partial: [String: String] = [:]
    private var candidates: [URL] = []
    private var lastFullScan: Date = .distantPast

    init(sessionsDir: URL) {
        self.sessionsDir = sessionsDir
    }

    /// Events appended since the previous poll (in file order, unsorted across files).
    func poll(now: Date = Date()) -> [LiveRateLimitEvent] {
        var out: [LiveRateLimitEvent] = []
        for file in recentFiles(now: now) {
            let path = file.path
            guard let attrs = try? FileManager.default.attributesOfItem(atPath: path),
                  let size = (attrs[.size] as? NSNumber)?.uint64Value else { continue }
            var offset: UInt64
            if let known = offsets[path] {
                offset = known > size ? 0 : known // truncated/rewritten file: start over
            } else {
                offset = size > initialTailBytes ? size - initialTailBytes : 0
            }
            guard size > offset else { offsets[path] = size; continue }
            guard let handle = FileHandle(forReadingAtPath: path) else { continue }
            defer { try? handle.close() }
            do { try handle.seek(toOffset: offset) } catch { continue }
            let data = handle.readData(ofLength: Int(min(size - offset, 8 * 1024 * 1024)))
            offsets[path] = offset + UInt64(data.count)
            let text = (partial[path] ?? "") + String(decoding: data, as: UTF8.self)
            var lines = text.components(separatedBy: "\n")
            partial[path] = lines.removeLast()
            for line in lines {
                if let ev = RolloutParser.parse(line: line) { out.append(ev) }
            }
        }
        return out
    }

    /// Newest main-limit event since the previous poll, plus the newest per additional limit id.
    func pollLatest(now: Date = Date()) -> (main: LiveRateLimitEvent?, extras: [String: LiveRateLimitEvent]) {
        var main: LiveRateLimitEvent?
        var extras: [String: LiveRateLimitEvent] = [:]
        for ev in poll(now: now) {
            if ev.isMainLimit {
                if main == nil || ev.timestamp > main!.timestamp { main = ev }
            } else if let id = ev.limitId {
                if let prev = extras[id], prev.timestamp >= ev.timestamp { continue }
                extras[id] = ev
            }
        }
        return (main, extras)
    }

    /// Threads can live for weeks and their rollout sits under the day they were *created*, so a
    /// periodic walk of every `YYYY/MM/DD` directory is needed (about 1000 files, a few dozen ms).
    private func fullScan(now: Date) -> [URL] {
        let fm = FileManager.default
        let keys: [URLResourceKey] = [.contentModificationDateKey, .isDirectoryKey]
        var out: [URL] = []
        func subdirs(_ url: URL) -> [URL] {
            (try? fm.contentsOfDirectory(at: url, includingPropertiesForKeys: [.isDirectoryKey], options: [.skipsHiddenFiles]))?
                .filter { (try? $0.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true } ?? []
        }
        for year in subdirs(sessionsDir) {
            for month in subdirs(year) {
                for day in subdirs(month) {
                    guard let items = try? fm.contentsOfDirectory(at: day, includingPropertiesForKeys: keys, options: [.skipsHiddenFiles]) else { continue }
                    for f in items where f.pathExtension == "jsonl" {
                        if let m = try? f.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate,
                           now.timeIntervalSince(m) < recentWindow {
                            out.append(f)
                        }
                    }
                }
            }
        }
        return out
    }

    private func recentFiles(now: Date) -> [URL] {
        if now.timeIntervalSince(lastFullScan) >= fullScanInterval {
            candidates = fullScan(now: now)
            lastFullScan = now
            return candidates
        }
        // Between full scans: known candidates plus anything new in today's directory.
        let today = dayDirectory(for: now, timeZone: .current)
        var seen = Set(candidates.map { $0.path })
        if let items = try? FileManager.default.contentsOfDirectory(at: today, includingPropertiesForKeys: [.contentModificationDateKey], options: [.skipsHiddenFiles]) {
            for f in items where f.pathExtension == "jsonl" && !seen.contains(f.path) {
                if let m = try? f.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate,
                   now.timeIntervalSince(m) < recentWindow {
                    candidates.append(f)
                    seen.insert(f.path)
                }
            }
        }
        return candidates
    }

    private func dayDirectory(for date: Date, timeZone: TimeZone) -> URL {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = timeZone
        let c = cal.dateComponents([.year, .month, .day], from: date)
        return sessionsDir.appendingPathComponent(String(format: "%04d/%02d/%02d", c.year ?? 0, c.month ?? 0, c.day ?? 0))
    }
}
