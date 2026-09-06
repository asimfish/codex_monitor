import AppKit
import Combine
import Foundation

@MainActor
final class QuotaMonitor: ObservableObject {
    static let shared = QuotaMonitor()

    struct Entry: Identifiable {
        var profile: Profile
        var isActive: Bool
        var snapshot: UsageSnapshot?
        /// True when `snapshot` came from the on-disk cache or a previous successful fetch.
        var isStale: Bool
        var error: String?
        var isFetching: Bool
        /// Newest account-wide limit seen in a session rollout (real-time, same data as the app).
        var liveEvent: LiveRateLimitEvent?
        /// Newest per-model limit events, keyed by `limit_id` (e.g. Spark).
        var liveExtras: [String: LiveRateLimitEvent] = [:]

        var id: String { profile.id }

        /// True when the live event should be displayed instead of the API snapshot: it is newer
        /// than the last fetch, or it is recent and the API still reports *less* usage for the
        /// same window (the usage endpoint can lag the per-response headers by a little).
        var showsLive: Bool {
            guard let live = liveEvent else { return false }
            guard let snap = snapshot else { return true }
            if live.timestamp > snap.fetchedAt { return true }
            guard snap.fetchedAt.timeIntervalSince(live.timestamp) < 120,
                  let lp = live.primary, let ap = snap.usage.rateLimit?.primaryWindow else { return false }
            let sameWindow = abs((lp.resetAt ?? 0) - (ap.resetAt ?? 0)) < 60
            return sameWindow && lp.usedPercent > ap.usedPercent
        }

        /// What the card shows: the live event when it is newer than the API snapshot.
        var displayRateLimit: RateLimit? {
            if showsLive, let live = liveEvent { return live.asRateLimit }
            return snapshot?.usage.rateLimit
        }

        /// Additional (per-model) limits, patched with live events when those are newer.
        var displayAdditionalLimits: [AdditionalRateLimit] {
            let base = snapshot?.usage.additionalRateLimits ?? []
            let fetched = snapshot?.fetchedAt ?? .distantPast
            return base.map { extra in
                guard let key = extra.meteredFeature, let live = liveExtras[key], live.timestamp > fetched else { return extra }
                var patched = extra
                patched.rateLimit = live.asRateLimit
                return patched
            }
        }

        /// Timestamp and origin of the numbers currently displayed.
        var dataSource: (live: Bool, at: Date)? {
            if showsLive, let live = liveEvent { return (true, live.timestamp) }
            if let s = snapshot { return (false, s.fetchedAt) }
            return nil
        }

        var primaryWindow: RateLimitWindow? {
            displayRateLimit?.windows.first
        }

        /// The tightest constraint right now (lowest remaining %) across the main windows.
        var tightestWindow: RateLimitWindow? {
            displayRateLimit?.windows.min { $0.remainingPercent < $1.remainingPercent }
        }

        var resetCreditCount: Int? {
            snapshot?.resetCredits?.availableCount ?? snapshot?.usage.rateLimitResetCredits?.availableCount
        }
    }

    @Published private(set) var entries: [Entry] = []
    @Published private(set) var lastRefresh: Date?
    @Published private(set) var isRefreshing = false
    @Published private(set) var recentMessages: [String] = []
    /// Bumped once a minute so relative times and countdowns re-render.
    @Published private(set) var clockTick = 0
    static let intervalChoices = [15, 30, 60, 120, 300]

    @Published var refreshIntervalSeconds: Int {
        didSet {
            UserDefaults.standard.set(refreshIntervalSeconds, forKey: "refreshIntervalSeconds")
            scheduleTimer()
        }
    }
    /// When the server rejects an access token (401), exchange the refresh token once, like Codex
    /// itself does. A rejected token is already useless everywhere, so this cannot break a copy
    /// on another machine; it only restores this one. Default on.
    @Published var autoRefreshOn401: Bool {
        didSet { UserDefaults.standard.set(autoRefreshOn401, forKey: "autoRefreshOn401") }
    }
    private var lastAutoRefreshAttempt: [String: Date] = [:]

    let store = ProfileStore.shared
    private var client: UsageClient
    private var refreshTimer: Timer?
    private var pollTimer: Timer?
    private var tickTimer: Timer?
    private var lastSignature = ""
    private var started = false
    /// Reset-credit details change rarely; re-fetch them at most this often per account.
    private let creditsMaxAge: TimeInterval = 180
    private var lastCreditsFetch: [String: Date] = [:]
    /// Set after an HTTP 429; automatic polling pauses until then (manual refresh still works).
    private var backoffUntil: Date?
    /// One tailer per `sessions` directory being watched, keyed by path.
    private var tailers: [String: RolloutTailer] = [:]
    private var liveTimer: Timer?
    /// Rollout events older than the last change of ~/.codex/auth.json may belong to a
    /// previously active account, so they are ignored for the main sessions directory.
    private var mainAuthModified: Date = .distantPast

    private init() {
        let d = UserDefaults.standard
        var seconds = d.integer(forKey: "refreshIntervalSeconds")
        if seconds <= 0 {
            // Migrate the pre-1.1 minutes setting; otherwise default to 30 s.
            let minutes = d.integer(forKey: "refreshIntervalMinutes")
            seconds = minutes > 0 ? min(minutes * 60, 300) : 30
        }
        refreshIntervalSeconds = seconds
        autoRefreshOn401 = d.object(forKey: "autoRefreshOn401") as? Bool ?? true
        client = UsageClient(baseURL: ProfileStore.shared.chatGPTBaseURL())
    }

    // MARK: Lifecycle

    func start() {
        guard !started else { return }
        started = true
        if DemoData.enabled {
            entries = DemoData.entries()
            lastRefresh = Date()
            tickTimer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
                Task { @MainActor in self?.clockTick += 1 }
            }
            return
        }
        store.ensureDirectories()
        reloadProfiles()
        Task { await refreshAll() }
        scheduleTimer()
        pollTimer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.pollFiles() }
        }
        tickTimer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.clockTick += 1 }
        }
        pollLive()
        liveTimer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.pollLive() }
        }
        NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didWakeNotification, object: nil, queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                try? await Task.sleep(nanoseconds: 4_000_000_000)
                await self?.refreshAll()
            }
        }
    }

    private func scheduleTimer() {
        refreshTimer?.invalidate()
        let interval = TimeInterval(max(10, refreshIntervalSeconds))
        refreshTimer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            Task { @MainActor in await self?.refreshAll(automatic: true) }
        }
        refreshTimer?.tolerance = min(5, interval / 10)
    }

    /// Refresh now unless data is younger than `seconds` (used when the panel becomes visible).
    func refreshIfStale(seconds: TimeInterval) async {
        if let last = lastRefresh, Date().timeIntervalSince(last) < seconds { return }
        await refreshAll()
    }

    // MARK: Profiles

    /// Re-scan disk. Existing snapshots are carried over by account id so the UI does not flicker.
    func reloadProfiles() {
        if DemoData.enabled { return }
        let main = store.loadMain()
        var profiles = store.loadProfiles()

        if let main = main, let msg = store.adopt(main: main, into: profiles) {
            log(msg)
            profiles = store.loadProfiles()
        }

        var previous: [String: Entry] = [:]
        for e in entries {
            if let acct = e.profile.accountId, previous[acct] == nil { previous[acct] = e }
        }

        mainAuthModified = main?.modified ?? .distantPast

        func carryOver(_ e: inout Entry) {
            guard let acct = e.profile.accountId else { return }
            if let prev = previous[acct] {
                e.snapshot = prev.snapshot
                e.isStale = prev.isStale
                e.error = prev.error
                e.liveEvent = prev.liveEvent
                e.liveExtras = prev.liveExtras
            } else if let cached = loadCache(accountId: acct) {
                e.snapshot = cached
                e.isStale = true
            }
        }

        var newEntries: [Entry] = []
        let mainAccount = main?.accountId
        for p in profiles {
            var e = Entry(profile: p, isActive: mainAccount != nil && p.accountId == mainAccount,
                          snapshot: nil, isStale: false, error: nil, isFetching: false)
            carryOver(&e)
            newEntries.append(e)
        }
        // ~/.codex/auth.json holding an account that is not stored as a profile yet.
        if let main = main, main.auth != nil, !profiles.contains(where: { $0.accountId == main.accountId }) {
            var e = Entry(profile: main, isActive: true, snapshot: nil, isStale: false, error: nil, isFetching: false)
            carryOver(&e)
            newEntries.insert(e, at: 0)
        }
        // Active account first, then alphabetical.
        newEntries.sort { a, b in
            if a.isActive != b.isActive { return a.isActive }
            return a.profile.name.localizedStandardCompare(b.profile.name) == .orderedAscending
        }
        entries = newEntries
        lastSignature = currentSignature()
    }

    private func currentSignature() -> String {
        var parts: [String] = []
        if let m = store.loadMain() { parts.append(m.signature) }
        parts.append(contentsOf: store.loadProfiles().map { $0.signature })
        return parts.joined(separator: "\n")
    }

    private func pollFiles() {
        let sig = currentSignature()
        guard sig != lastSignature else { return }
        log(L.authChanged)
        reloadProfiles()
        Task { await refreshAll() }
    }

    // MARK: Live rollout events

    /// Directories whose rollouts describe this entry's account: the shared `~/.codex/sessions`
    /// for whichever account is active, plus the profile's own `sessions` (from `codex-acct run`).
    private func sessionsDirs(for entry: Entry) -> [(dir: URL, isMain: Bool)] {
        var dirs: [(URL, Bool)] = []
        if entry.isActive {
            dirs.append((store.codexHome.appendingPathComponent("sessions"), true))
        }
        if !entry.profile.isMain {
            let own = entry.profile.directory.appendingPathComponent("sessions")
            if FileManager.default.fileExists(atPath: own.path) { dirs.append((own, false)) }
        }
        return dirs
    }

    private func pollLive() {
        let now = Date()
        for idx in entries.indices {
            for (dir, isMain) in sessionsDirs(for: entries[idx]) {
                let tailer: RolloutTailer
                if let existing = tailers[dir.path] {
                    tailer = existing
                } else {
                    tailer = RolloutTailer(sessionsDir: dir)
                    tailers[dir.path] = tailer
                }
                let latest = tailer.pollLatest(now: now)
                if let ev = latest.main, !(isMain && ev.timestamp < mainAuthModified),
                   ev.timestamp > (entries[idx].liveEvent?.timestamp ?? .distantPast) {
                    entries[idx].liveEvent = ev
                    let used = ev.primary?.usedPercent ?? ev.secondary?.usedPercent ?? -1
                    NSLog("[CodexMonitor] live rate-limit event for %@: used %.1f%% at %@ (applied %.1fs later)",
                          entries[idx].profile.displayName, used, ISO8601.format(ev.timestamp), now.timeIntervalSince(ev.timestamp))
                }
                for (id, ev) in latest.extras where !(isMain && ev.timestamp < mainAuthModified) {
                    if ev.timestamp > (entries[idx].liveExtras[id]?.timestamp ?? .distantPast) {
                        entries[idx].liveExtras[id] = ev
                    }
                }
            }
        }
    }

    // MARK: Fetching

    /// `automatic` refreshes respect the 429 back-off; manual ones always go through.
    func refreshAll(automatic: Bool = false) async {
        guard !isRefreshing, !DemoData.enabled else { return }
        if automatic, let until = backoffUntil, until > Date() { return }
        isRefreshing = true
        defer { isRefreshing = false }
        client = UsageClient(baseURL: store.chatGPTBaseURL())
        let ids = entries.map { $0.id }
        await withTaskGroup(of: Void.self) { group in
            for id in ids {
                group.addTask { @MainActor in await self.refresh(entryId: id) }
            }
        }
        lastRefresh = Date()
    }

    func refresh(entryId: String, allowAutoRefresh: Bool = true) async {
        guard let idx = entries.firstIndex(where: { $0.id == entryId }) else { return }
        let profile = entries[idx].profile
        guard profile.isChatGPTAuth, let token = profile.accessToken else {
            entries[idx].error = profile.loadError ?? UsageError.noToken.localizedDescription
            return
        }
        if profile.identity?.accessTokenExpired == true {
            if allowAutoRefresh {
                switch await autoRefreshIfAllowed(entryId: entryId) {
                case .refreshed:
                    await refresh(entryId: entryId, allowAutoRefresh: false)
                    return
                case .failed:
                    return
                case .skipped:
                    break
                }
            }
            let exp = profile.identity?.accessTokenExpiry.map { Fmt.dateTime.string(from: $0) } ?? "?"
            if let i = entries.firstIndex(where: { $0.id == entryId }) {
                entries[i].error = L.tokenExpiredNeedRefresh(exp)
            }
            return
        }
        entries[idx].isFetching = true
        let client = self.client
        let acct = profile.accountId
        let previousCredits = entries[idx].snapshot?.resetCredits
        let creditsKey = acct ?? entryId
        let creditsDue = previousCredits == nil
            || Date().timeIntervalSince(lastCreditsFetch[creditsKey] ?? .distantPast) > creditsMaxAge
        do {
            let usage = try await client.fetchUsage(accessToken: token, accountId: acct)
            var credits = previousCredits
            if creditsDue {
                if let fresh = try? await client.fetchResetCredits(accessToken: token, accountId: acct) {
                    credits = fresh
                    lastCreditsFetch[creditsKey] = Date()
                }
            }
            let snap = UsageSnapshot(fetchedAt: Date(), usage: usage, resetCredits: credits)
            if let i = entries.firstIndex(where: { $0.id == entryId }) {
                entries[i].snapshot = snap
                entries[i].isStale = false
                entries[i].error = nil
                entries[i].isFetching = false
            }
            if let acct = acct { saveCache(snap, accountId: acct) }
        } catch {
            if case UsageError.http(let code, _) = error, code == 429 {
                backoffUntil = Date().addingTimeInterval(120)
                log(L.rateLimitedBackoff)
            }
            if let ue = error as? UsageError, ue.isAuthFailure, allowAutoRefresh {
                if let i = entries.firstIndex(where: { $0.id == entryId }) { entries[i].isFetching = false }
                switch await autoRefreshIfAllowed(entryId: entryId) {
                case .refreshed:
                    await refresh(entryId: entryId, allowAutoRefresh: false)
                    return
                case .failed:
                    return  // error text already set to "session revoked"
                case .skipped:
                    break
                }
            }
            if let i = entries.firstIndex(where: { $0.id == entryId }) {
                entries[i].error = (error as? UsageError)?.localizedDescription ?? error.localizedDescription
                entries[i].isFetching = false
                if entries[i].snapshot != nil { entries[i].isStale = true }
            }
        }
    }

    private enum AutoRefreshOutcome { case refreshed, failed, skipped }

    /// One refresh-token exchange per account per 10 minutes, only when enabled. On success the
    /// new tokens are written to the profile (and to ~/.codex/auth.json when that profile is the
    /// active one) and the profiles are reloaded so the retry uses them.
    private func autoRefreshIfAllowed(entryId: String) async -> AutoRefreshOutcome {
        guard autoRefreshOn401, let idx = entries.firstIndex(where: { $0.id == entryId }) else { return .skipped }
        let entry = entries[idx]
        let profile = entry.profile
        guard let rt = profile.auth?.tokens?.refreshToken, !rt.isEmpty else { return .skipped }
        let key = profile.accountId ?? profile.id
        if let last = lastAutoRefreshAttempt[key], Date().timeIntervalSince(last) < 600 { return .skipped }
        lastAutoRefreshAttempt[key] = Date()
        do {
            let refreshed = try await client.refreshTokens(refreshToken: rt)
            try store.applyRefreshedTokens(refreshed, to: profile.authURL)
            if entry.isActive, !profile.isMain {
                try store.applyRefreshedTokens(refreshed, to: store.mainAuthURL)
            }
            log(L.autoRefreshed(profile.displayName))
            reloadProfiles()
            return .refreshed
        } catch {
            log(L.autoRefreshFailed(profile.displayName, error.localizedDescription))
            if let i = entries.firstIndex(where: { $0.id == entryId }) {
                entries[i].error = L.sessionRevoked
                entries[i].isFetching = false
                if entries[i].snapshot != nil { entries[i].isStale = true }
            }
            return .failed
        }
    }

    // MARK: Actions

    func activate(entryId: String) {
        guard let entry = entries.first(where: { $0.id == entryId }), !entry.profile.isMain else { return }
        let main = store.loadMain()
        let profiles = store.loadProfiles()
        do {
            let msgs = try store.activate(entry.profile, currentMain: main, profiles: profiles)
            msgs.forEach { log($0) }
        } catch {
            log(L.switchFailed(error.localizedDescription))
        }
        reloadProfiles()
        Task { await refreshAll() }
    }

    func saveMainAsProfile(named name: String) {
        do {
            let p = try store.saveMainAsProfile(named: name)
            log(L.savedAsProfile(p.name))
        } catch {
            log(L.saveFailed(error.localizedDescription))
        }
        reloadProfiles()
        Task { await refreshAll() }
    }

    /// Explicit, user-confirmed token refresh. Writes the profile's auth.json and, when that
    /// profile is the active one, `~/.codex/auth.json` as well so both copies stay in lock-step.
    func refreshTokens(entryId: String) async {
        guard let idx = entries.firstIndex(where: { $0.id == entryId }) else { return }
        let profile = entries[idx].profile
        guard let rt = profile.auth?.tokens?.refreshToken, !rt.isEmpty else {
            log(L.noRefreshToken(profile.displayName))
            return
        }
        entries[idx].isFetching = true
        do {
            let refreshed = try await client.refreshTokens(refreshToken: rt)
            try store.applyRefreshedTokens(refreshed, to: profile.authURL)
            if entries[idx].isActive, !profile.isMain {
                try store.applyRefreshedTokens(refreshed, to: store.mainAuthURL)
            }
            log(L.tokensRefreshed(profile.displayName))
        } catch {
            log(L.refreshFailed(profile.displayName, error.localizedDescription))
        }
        if let i = entries.firstIndex(where: { $0.id == entryId }) { entries[i].isFetching = false }
        reloadProfiles()
        await refreshAll()
    }

    // MARK: Derived

    var activeEntry: Entry? { entries.first { $0.isActive } }

    var menuBarTitle: String {
        guard let e = activeEntry else { return entries.isEmpty ? "-" : "?" }
        if let w = e.tightestWindow {
            let pct = Fmt.percent(w.remainingPercent)
            return e.isStale ? "\(pct)*" : pct
        }
        if e.error != nil { return "!" }
        return "..."
    }

    // MARK: Cache

    private func cacheURL(accountId: String) -> URL {
        store.cacheDir.appendingPathComponent("usage-\(accountId).json")
    }

    private func loadCache(accountId: String) -> UsageSnapshot? {
        guard let data = try? Data(contentsOf: cacheURL(accountId: accountId)) else { return nil }
        let dec = JSONDecoder()
        dec.dateDecodingStrategy = .iso8601
        return try? dec.decode(UsageSnapshot.self, from: data)
    }

    private func saveCache(_ snap: UsageSnapshot, accountId: String) {
        let enc = JSONEncoder()
        enc.dateEncodingStrategy = .iso8601
        enc.outputFormatting = [.prettyPrinted, .sortedKeys]
        guard let data = try? enc.encode(snap) else { return }
        try? store.writeAtomically(data, to: cacheURL(accountId: accountId))
    }

    // MARK: Log

    /// UI-originated events (copy/export) that should appear in the panel log.
    func note(_ message: String) { log(message) }

    private func log(_ message: String) {
        let stamp = Fmt.dateTime.string(from: Date())
        recentMessages.insert("\(stamp) \(message)", at: 0)
        if recentMessages.count > 20 { recentMessages.removeLast(recentMessages.count - 20) }
        NSLog("[CodexMonitor] %@", message)
    }
}
