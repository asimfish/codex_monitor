import Foundation

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

    private var offsets: [String: UInt64] = [:]
    private var partial: [String: String] = [:]

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

    private func recentFiles(now: Date) -> [URL] {
        var dirs = Set<URL>()
        let cal = Calendar.current
        for dayOffset in 0...1 {
            if let d = cal.date(byAdding: .day, value: -dayOffset, to: now) {
                dirs.insert(dayDirectory(for: d, timeZone: .current))
            }
        }
        dirs.insert(dayDirectory(for: now, timeZone: TimeZone(identifier: "UTC")!))
        var files: [URL] = []
        for dir in dirs {
            guard let items = try? FileManager.default.contentsOfDirectory(
                at: dir, includingPropertiesForKeys: [.contentModificationDateKey], options: [.skipsHiddenFiles]
            ) else { continue }
            for f in items where f.pathExtension == "jsonl" {
                if let m = try? f.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate,
                   now.timeIntervalSince(m) < recentWindow {
                    files.append(f)
                }
            }
        }
        return files
    }

    private func dayDirectory(for date: Date, timeZone: TimeZone) -> URL {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = timeZone
        let c = cal.dateComponents([.year, .month, .day], from: date)
        return sessionsDir.appendingPathComponent(String(format: "%04d/%02d/%02d", c.year ?? 0, c.month ?? 0, c.day ?? 0))
    }
}
