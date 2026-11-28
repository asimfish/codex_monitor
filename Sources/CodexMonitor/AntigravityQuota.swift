import AppKit
import SwiftUI

struct AntigravityQuota: Decodable {
    struct Group: Decodable {
        let name: String
        let buckets: [Bucket]
        var description: String? = nil
    }

    struct Bucket: Decodable {
        let name: String
        let window: String?
        let remainingFraction: Double?
        let resetTime: String?
        let disabled: Bool?

        var title: String {
            switch window {
            case "5h": return "5 小时"
            case "weekly": return "每周"
            default: return name
            }
        }

        var fraction: Double? {
            remainingFraction.flatMap { (0...1).contains($0) ? $0 : nil }
        }

        var reportedResetDate: Date? { resetTime.flatMap(ISO8601.parse) }
        var resetDate: Date? { reportedResetDate.flatMap { $0 > Date() ? $0 : nil } }
    }

    let groups: [Group]
    var remainingCredits: Int? = nil

    private enum CodingKeys: String, CodingKey { case groups }

    static func decodeCredits(_ data: Data) -> Int? {
        struct Credits: Decodable {
            struct Command: Decodable {
                struct Payload: Decodable { let remainingCredits: Int }
                let name: String
                let data: Payload
            }
            let status: String
            let numTurns: Int
            let command: Command
        }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        guard let reply = try? decoder.decode(Credits.self, from: data),
              reply.status == "SUCCESS", reply.numTurns == 0, reply.command.name == "credits",
              reply.command.data.remainingCredits >= 0 else { return nil }
        return reply.command.data.remainingCredits
    }

    static func tightestGeminiBucket(in groups: [Group]) -> Bucket? {
        groups.filter { $0.name.localizedCaseInsensitiveContains("Gemini") }
            .flatMap(\.buckets)
            .compactMap { bucket -> (Bucket, Double)? in
                guard bucket.disabled != true, let fraction = bucket.fraction else { return nil }
                return (bucket, fraction)
            }
            .min { $0.1 < $1.1 }?.0
    }

    static func planBadgeLabel(for plan: String) -> String {
        let trimmed = plan.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return "" }
        let lower = trimmed.lowercased()
        if lower == "free" || lower == "antigravity free" || lower == "free tier" { return "Free" }
        if lower == "pro" || lower == "google ai pro" || lower == "antigravity pro" { return "Pro" }
        if lower == "plus" || lower == "google ai plus" { return "Plus" }
        if lower == "ultra" || lower == "google ai ultra" { return "Ultra" }
        if lower == "standard" || lower == "gemini code assist standard" || lower == "standard tier" { return "Standard" }
        if lower == "enterprise" || lower == "gemini enterprise"
            || lower == "gemini code assist enterprise"
            || lower.hasPrefix("gemini enterprise ") { return "Enterprise" }
        for prefix in ["Google AI ", "Antigravity ", "Gemini Code Assist "] {
            if lower.hasPrefix(prefix.lowercased()) {
                let suffix = String(trimmed.dropFirst(prefix.count)).trimmingCharacters(in: .whitespaces)
                if !suffix.isEmpty { return suffix }
            }
        }
        return trimmed
    }

    private struct Reply: Decodable {
        struct Command: Decodable {
            let name: String
            let data: AntigravityQuota
        }

        let status: String
        let numTurns: Int
        let command: Command
    }

    static func decode(_ data: Data) -> Self? {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        guard let reply = try? decoder.decode(Reply.self, from: data),
              reply.status == "SUCCESS", reply.numTurns == 0, reply.command.name == "usage",
              reply.command.data.groups.count <= 24,
              reply.command.data.groups.allSatisfy({ $0.buckets.count <= 16 }) else { return nil }
        return reply.command.data
    }
}

enum AntigravityCLI {
    struct Identity: Decodable {
        let email: String?
        let planTier: String?
    }

    struct Eligibility: Error {
        static let message = "Google 已登录，需完成 Antigravity 账号验证后才能读取额度。"
        let url: URL?
    }

    private struct Binary: Equatable {
        let path: URL
        let version: String
        let modified: Date
        let size: Int
    }

    private static let isolationLock = NSLock()
    private static var verifiedBinary: Binary?

    enum Failure: String, Error {
        case missing = "安装官方 Antigravity CLI（agy）后即可同步额度。"
        case version = "请将 Antigravity CLI 更新到 1.1.11 或更新版本。"
        case login = "请登录 Antigravity，然后点刷新。"
        case profilesUnsupported = "此 agy 版本无法验证账号隔离。请更新到 1.5.17 或更高版本。"
        case unavailable = "当前登录方式没有返回可显示的模型额度。"
        case failed = "额度读取失败，请检查 Antigravity 登录状态和网络后刷新。"
    }

    static func executable() throws -> URL {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let paths = [home.appendingPathComponent(".local/bin/agy"),
                     URL(fileURLWithPath: "/opt/homebrew/bin/agy"),
                     URL(fileURLWithPath: "/usr/local/bin/agy")]
        guard let command = paths.first(where: { FileManager.default.isExecutableFile(atPath: $0.path) }) else {
            throw Failure.missing
        }
        return command
    }

    private static func versionNumbers(_ version: String) -> [Int] {
        let parts = version.trimmingCharacters(in: .whitespacesAndNewlines)
            .split(separator: ".", omittingEmptySubsequences: false)
        return parts.count == 3 ? parts.compactMap { Int($0) } : []
    }

    static func supportsQuota(_ version: String) -> Bool {
        let numbers = versionNumbers(version)
        guard numbers.count == 3, numbers.allSatisfy({ $0 >= 0 }) else { return false }
        // v1.1.11 introduced read-only /usage in print mode. Older versions could submit it as a prompt.
        return !numbers.lexicographicallyPrecedes([1, 1, 11])
    }

    private static func supportsProfiles(_ version: String) -> Bool {
        let numbers = versionNumbers(version)
        return numbers.count == 3 && numbers.allSatisfy { $0 >= 0 } &&
            !numbers.lexicographicallyPrecedes([1, 5, 16])
    }

    static func readQuota(profile: URL? = nil) throws -> AntigravityQuota {
        let command = try executable()
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("codex-monitor-agy-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        defer { try? FileManager.default.removeItem(at: directory) }
        let version = try run(command, ["--version"], in: directory)
        let versionText = String(decoding: version.output, as: UTF8.self)
        guard version.status == 0, supportsQuota(versionText) else {
            throw Failure.version
        }
        if profile != nil {
            guard supportsProfiles(versionText) else { throw Failure.profilesUnsupported }
            try verifyIsolation(command, version: versionText, in: directory)
        }
        var quota = try query(command, profile: profile, in: directory)
        let arguments = profileArguments(profile) + ["-p", "/credits", "--output-format", "json", "--print-timeout", "6s"]
        if let credits = try? run(command, arguments, in: directory, isolated: profile != nil, timeout: 9), credits.status == 0 {
            quota.remainingCredits = AntigravityQuota.decodeCredits(credits.output)
        }
        return quota
    }

    static func verifyIsolation() throws -> URL {
        let command = try executable()
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("codex-monitor-agy-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        defer { try? FileManager.default.removeItem(at: directory) }
        let version = try run(command, ["--version"], in: directory)
        let versionText = String(decoding: version.output, as: UTF8.self)
        guard version.status == 0, supportsProfiles(versionText) else {
            throw Failure.profilesUnsupported
        }
        try verifyIsolation(command, version: versionText, in: directory)
        return command
    }

    private static func verifyIsolation(_ command: URL, version: String, in directory: URL) throws {
        // Repeat the empty-profile check after the executable is replaced or updated.
        let path = command.resolvingSymlinksInPath()
        let metadata = try path.resourceValues(forKeys: [.fileSizeKey, .contentModificationDateKey])
        guard let modified = metadata.contentModificationDate, let size = metadata.fileSize else {
            throw Failure.profilesUnsupported
        }
        let binary = Binary(path: path, version: version, modified: modified, size: size)
        isolationLock.lock()
        defer { isolationLock.unlock() }
        if verifiedBinary == binary { return }
        let empty = directory.appendingPathComponent("empty-profile", isDirectory: true)
        try FileManager.default.createDirectory(at: empty, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        do {
            _ = try query(command, profile: empty, in: directory)
        } catch Failure.login {
            // An empty profile must require its own login, even when the system account is signed in.
            verifiedBinary = binary
            return
        } catch Failure.failed {
            throw Failure.failed
        } catch {
            throw Failure.profilesUnsupported
        }
        throw Failure.profilesUnsupported
    }

    private static func profileArguments(_ profile: URL?) -> [String] {
        profile.map { ["--gemini_dir", $0.path, "--app_data_dir", "antigravity-cli"] } ?? []
    }

    private static func query(_ command: URL, profile: URL?, in directory: URL) throws -> AntigravityQuota {
        let arguments = profileArguments(profile) + ["-p", "/usage", "--output-format", "json", "--print-timeout", "15s"]
        let result = try run(command, arguments, in: directory, isolated: profile != nil)
        if result.status != 0 {
            let output = String(decoding: result.output + result.diagnostics, as: UTF8.self)
            struct Reply: Decodable { let error: String }
            let detail = (try? JSONDecoder().decode(Reply.self, from: result.output))?.error
            if let eligibility = eligibilityFailure(detail ?? output) { throw eligibility }
            let message = output.lowercased()
            if message.contains("authentication required") || message.contains("not authenticated") {
                throw Failure.login
            }
            throw Failure.failed
        }
        guard let quota = AntigravityQuota.decode(result.output), !quota.groups.isEmpty,
              quota.groups.contains(where: { !$0.buckets.isEmpty }) else { throw Failure.unavailable }
        return quota
    }

    static func eligibilityFailure(_ message: String) -> Eligibility? {
        guard message.localizedCaseInsensitiveContains("eligibility check failed") else { return nil }
        let url = message.split(whereSeparator: \.isWhitespace).lazy.compactMap { token -> URL? in
            guard let parts = URLComponents(string: String(token)),
                  parts.scheme == "https", parts.host == "accounts.google.com",
                  parts.path == "/v3/signin/continue", parts.user == nil,
                  parts.password == nil, parts.port == nil, parts.fragment == nil,
                  parts.queryItems?.filter({ $0.name == "continue" }).map(\.value) ==
                    ["https://developers.google.com/gemini-code-assist/auth/auth_success_gemini"] else { return nil }
            return parts.url
        }.first
        return Eligibility(url: url)
    }

    static func readIdentity(profile: AntigravityAccount? = nil) -> Identity? {
        guard let command = try? executable() else { return nil }
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("codex-monitor-agy-" + UUID().uuidString)
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
                                                    attributes: [.posixPermissions: 0o700])
            defer { try? FileManager.default.removeItem(at: directory) }
            let helper = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".local/bin/codex-monitor")
            var arguments = ["antigravity-identity", command.path]
            if let profile {
                _ = try verifyIsolation()
                guard profile.hasToken else { return nil }
                arguments += ["--profile", profile.id.uuidString]
            }
            let result = try run(helper, arguments, in: directory)
            guard result.status == 0 else { return nil }
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            return try decoder.decode(Identity.self, from: result.output)
        } catch {
            return nil
        }
    }

    private static func run(_ executable: URL, _ arguments: [String], in directory: URL,
                            isolated: Bool = false, timeout seconds: Double = 25)
        throws -> (status: Int32, output: Data, diagnostics: Data) {
        let stdout = directory.appendingPathComponent("out")
        let stderr = directory.appendingPathComponent("err")
        try Data().write(to: stdout)
        try Data().write(to: stderr)
        let out = try FileHandle(forUpdating: stdout)
        defer { try? out.close() }
        let err = try FileHandle(forUpdating: stderr)
        defer { try? err.close() }
        let process = Process()
        process.executableURL = executable
        process.arguments = arguments
        process.currentDirectoryURL = directory
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = out
        process.standardError = err
        let allowedEnvironment: Set<String> = ["HOME", "TMPDIR", "PATH", "USER", "LOGNAME",
                                               "LANG", "LC_ALL", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
                                               "https_proxy", "http_proxy", "no_proxy"]
        var environment = ProcessInfo.processInfo.environment.filter { allowedEnvironment.contains($0.key) }
        environment["AGY_CLI_DISABLE_AUTO_UPDATE"] = "true"
        if isolated { environment["AGY_CLI_FORCE_FILE_TOKEN_STORAGE"] = "1" }
        process.environment = environment
        try process.run()
        let timeout = DispatchWorkItem {
            if process.isRunning { process.terminate() }
        }
        let kill = DispatchWorkItem {
            if process.isRunning { Darwin.kill(process.processIdentifier, SIGKILL) }
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + seconds, execute: timeout)
        DispatchQueue.global().asyncAfter(deadline: .now() + seconds + 2, execute: kill)
        process.waitUntilExit()
        timeout.cancel()
        kill.cancel()
        try out.seek(toOffset: 0)
        try err.seek(toOffset: 0)
        let limit = 256 * 1024
        let output = try out.read(upToCount: limit + 1) ?? Data()
        guard output.count <= limit else { throw Failure.failed }
        return (process.terminationStatus, output, try err.read(upToCount: 4096) ?? Data())
    }
}

@MainActor
final class AntigravityModel: ObservableObject {
    struct SystemSwitch {
        let accountID: UUID
        let email: String
        var needsRetry = false
    }

    static let shared = AntigravityModel()
    private static let activeAccountKey = "antigravityStripAccountID"
    private static let previousSystemEmailKey = "antigravityPreviousSystemEmail"
    @Published private(set) var groups: [AntigravityQuota.Group] = []
    @Published private(set) var lastSync: Date?
    @Published private(set) var error: String?
    @Published private(set) var systemEmail: String?
    @Published private(set) var systemPlan: String?
    @Published private(set) var systemCredits: Int?
    @Published private(set) var previousSystemEmail = UserDefaults.standard.string(forKey: AntigravityModel.previousSystemEmailKey)
    @Published private(set) var systemEligibility: AntigravityCLI.Eligibility?
    @Published private(set) var systemQuotaIsStale = false
    private var systemQuotaEmail: String?
    private var systemPlanEmail: String?
    @Published private(set) var busy = false
    @Published private(set) var accounts = AntigravityAccount.load()
    @Published private(set) var saved: [UUID: AntigravityAccountStatus] = [:]
    @Published private var accountOrder = AccountOrder(keyPrefix: "antigravityAccount")
    @Published private var activeAccountID = UserDefaults.standard.string(forKey: AntigravityModel.activeAccountKey)
        .flatMap(UUID.init(uuidString:))
    @Published private(set) var systemSwitch: SystemSwitch?
    @Published private(set) var signingIn: Set<UUID> = []
    private var loginProcess: Process?
    @Published private(set) var loginURL: URL?
    @Published private(set) var loginHint: String?
    @Published private(set) var submittingCode = false
    @Published private(set) var loginResult: AntigravitySignInResult?
    private var loginInput: FileHandle?
    private var clipboardVersion = 0
    private var loginCancelled = false
    private var polling: Task<Void, Never>?

    var canChangeCredentials: Bool { !busy && signingIn.isEmpty && !DemoData.enabled }
    var usesAutomaticAccountOrder: Bool { accountOrder.automatic }

    private var accountPriorities: [String: Int] {
        Dictionary(accounts.map { account in
            let status = saved[account.id]
            let remaining = AntigravityQuota.tightestGeminiBucket(in: status?.groups ?? [])?.fraction
            let priority = account.annotation?.unavailable == true ? 4 : AccountOrder.priority(
                remaining: remaining, expired: !account.hasToken,
                hasError: status?.error != nil || status?.eligibility != nil, limited: false)
            return (account.id.uuidString, priority)
        }, uniquingKeysWith: { first, _ in first })
    }

    var orderedAccounts: [AntigravityAccount] {
        let byID = Dictionary(accounts.map { ($0.id.uuidString, $0) }, uniquingKeysWith: { first, _ in first })
        let ordered = accountOrder.sorted(accounts.map { $0.id.uuidString }, priorities: accountPriorities).compactMap { byID[$0] }
        return ordered.filter { $0.annotation?.unavailable != true } + ordered.filter { $0.annotation?.unavailable == true }
    }

    func moveAccount(_ account: AntigravityAccount, direction: AccountOrder.Move) {
        let pinned = activeAccount.map { Set([$0.id.uuidString]) } ?? []
        accountOrder.move(account.id.uuidString, direction: direction, available: accounts.map { $0.id.uuidString },
                          pinned: pinned, priorities: accountPriorities)
    }

    func restoreAutomaticAccountOrder() { accountOrder.useAutomaticOrder() }

    var activeAccount: AntigravityAccount? {
        accounts.first { $0.id == activeAccountID && $0.hasToken }
    }

    var activeStatus: AntigravityAccountStatus? {
        if let account = activeAccount { return saved[account.id] }
        return systemStatus
    }

    var systemStatus: AntigravityAccountStatus {
        AntigravityAccountStatus(groups: groups, sync: lastSync, error: error, eligibility: systemEligibility,
                                email: systemEmail, planTier: systemPlan, remainingCredits: systemCredits)
    }

    func selectActiveAccount(_ account: AntigravityAccount?) {
        if let account {
            guard accounts.contains(where: { $0.id == account.id }), account.hasToken,
                  !signingIn.contains(account.id) else { return }
        }
        guard activeAccountID != account?.id else { return }
        storeActiveAccount(account?.id)
        if let account {
            Task { await refreshSavedAccount(account) }
        } else {
            Task { await refreshSystemAccount() }
        }
    }

    private func storeActiveAccount(_ id: UUID?) {
        activeAccountID = id
        UserDefaults.standard.set(id?.uuidString, forKey: Self.activeAccountKey)
    }

    func beginSystemSwitch(_ account: AntigravityAccount) {
        guard account.hasToken, !signingIn.contains(account.id),
              accounts.contains(where: { $0.id == account.id }), let email = saved[account.id]?.email else {
            PanelController.shared.info(title: "切换官方登录", message: "请先登录此独立账号并刷新，确认它的 Google 邮箱。")
            return
        }
        guard systemEmail?.caseInsensitiveCompare(email) != .orderedSame else {
            selectActiveAccount(nil)
            PanelController.shared.info(title: "切换官方登录", message: "官方客户端已登录 \(email)。")
            return
        }
        guard PanelController.shared.confirm(title: "切换官方登录到「\(account.name)」",
                                             message: "在 Antigravity 的设置 → Account 退出当前登录，再登录 \(email)。完成后回到悬浮窗点「核对登录」。",
                                             okTitle: "打开官方客户端") else { return }
        systemSwitch = SystemSwitch(accountID: account.id, email: email)
        openLogin()
    }

    func cancelSystemSwitch() { systemSwitch = nil }

    func start() {
        guard polling == nil else { return }
        if DemoData.enabled {
            groups = DemoData.antigravityGroups()
            systemEmail = "gemini@example.com"
            systemPlan = "Google AI Pro"
            systemCredits = 120
            lastSync = Date()
            return
        }
        polling = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refresh()
                try? await Task.sleep(nanoseconds: 300_000_000_000)
            }
        }
    }

    func refresh() async {
        guard !busy, !DemoData.enabled else { return }
        busy = true
        defer { busy = false }
        let activeID = activeAccount?.id
        if let active = activeAccount, !signingIn.contains(active.id) {
            await refresh(active)
        }
        await readSystemAccount()
        for account in accounts where account.id != activeID && !signingIn.contains(account.id) {
            await refresh(account)
        }
    }

    func refreshSystemAccount() async {
        guard !busy, !DemoData.enabled else { return }
        busy = true
        defer { busy = false }
        await readSystemAccount()
    }

    private func readSystemAccount() async {
        let checkingSwitch = systemSwitch != nil
        let identity = Task.detached(priority: .utility) { AntigravityCLI.readIdentity() }
        // During a switch, begin the quota query after reading the new identity.
        // Ordinary polling can fetch both in parallel.
        let before = checkingSwitch ? await identity.value : nil
        let quota = await Task.detached(priority: .utility) { try AntigravityCLI.readQuota() }.result
        let account = await identity.value
        if checkingSwitch, before?.email != nil {
            let after = await Task.detached(priority: .utility) { AntigravityCLI.readIdentity() }.value
            guard before?.email == after?.email else {
                applySystemRefresh(.failure(AntigravityCLI.Failure.failed), email: after?.email, plan: after?.planTier)
                return
            }
        }
        applySystemRefresh(quota, email: account?.email, plan: account?.planTier)
    }

    func applySystemRefresh(_ result: Result<AntigravityQuota, Error>, email: String?, plan: String? = nil) {
        systemEmail = email
        let normalizedPlan = plan?.trimmingCharacters(in: .whitespacesAndNewlines)
        if let normalizedPlan, !normalizedPlan.isEmpty {
            systemPlan = normalizedPlan
            systemPlanEmail = email
        } else if let email, systemPlanEmail?.caseInsensitiveCompare(email) == .orderedSame {
            // Keep the last confirmed tier for the same verified system account.
        } else {
            systemPlan = nil
            systemPlanEmail = nil
        }
        if let email, email != previousSystemEmail {
            previousSystemEmail = email
            UserDefaults.standard.set(email, forKey: Self.previousSystemEmailKey)
        }
        switch result {
        case .success(let quota):
            groups = quota.groups.filter { !$0.buckets.isEmpty }
            systemCredits = quota.remainingCredits
            lastSync = Date()
            systemQuotaEmail = email
            error = nil
            systemEligibility = nil
            systemQuotaIsStale = false
        case .failure(let failure):
            let quotaFailure = failure as? AntigravityCLI.Failure ?? .failed
            systemEligibility = failure as? AntigravityCLI.Eligibility
            error = systemEligibility == nil ? quotaFailure.rawValue : nil
            // A temporary error can reuse a snapshot only after confirming its account.
            systemQuotaIsStale = systemEligibility == nil && quotaFailure == .failed
                && email != nil && email == systemQuotaEmail && lastSync != nil
            if !systemQuotaIsStale {
                groups = []
                systemCredits = nil
                lastSync = nil
                systemQuotaEmail = nil
            }
        }
        if let pending = systemSwitch {
            let verified: Bool
            switch result {
            case .success: verified = true
            case .failure(let error): verified = error is AntigravityCLI.Eligibility
            }
            if verified && email?.caseInsensitiveCompare(pending.email) == .orderedSame {
                systemSwitch = nil
                storeActiveAccount(nil)
            } else {
                systemSwitch?.needsRetry = true
            }
        }
    }

    private func refresh(_ account: AntigravityAccount) async {
        guard account.hasToken else {
            saved[account.id] = nil
            return
        }
        let identity = Task.detached(priority: .utility) { AntigravityCLI.readIdentity(profile: account) }
        let result = await Task.detached(priority: .utility) { try AntigravityCLI.readQuota(profile: account.directory) }.result
        let identityResult = await identity.value
        let previous = saved[account.id]
        let email = identityResult?.email
        let reportedPlan = identityResult?.planTier?.trimmingCharacters(in: .whitespacesAndNewlines)
        let preservedPlan = (email != nil && previous?.email?.caseInsensitiveCompare(email!) == .orderedSame)
            ? previous?.planTier : nil
        let planTier = (reportedPlan?.isEmpty == false ? reportedPlan : nil) ?? preservedPlan
        var status = AntigravityAccountStatus(email: email, planTier: planTier)
        switch result {
        case .success(let quota):
            status.groups = quota.groups.filter { !$0.buckets.isEmpty }
            status.sync = Date()
            status.remainingCredits = quota.remainingCredits
        case .failure(let failure):
            if let eligibility = failure as? AntigravityCLI.Eligibility {
                status.eligibility = eligibility
            } else {
                status.error = (failure as? AntigravityCLI.Failure ?? .failed).rawValue
            }
        }
        saved[account.id] = status
    }

    func refreshSavedAccount(_ account: AntigravityAccount) async {
        guard !busy, !signingIn.contains(account.id), !DemoData.enabled else { return }
        busy = true
        defer { busy = false }
        await refresh(account)
    }

    private func validName(_ name: String, excluding id: UUID? = nil) -> Bool {
        (1...80).contains(name.count) && !name.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains)
            && !accounts.contains { $0.id != id && $0.name.caseInsensitiveCompare(name) == .orderedSame }
    }

    func addAccount() {
        let controller = PanelController.shared
        guard let name = controller.prompt(title: "添加 Antigravity 账号",
                                           message: "给账号起个名字。完成 Google 授权后，在 Antigravity 登录窗口提交授权码。",
                                           defaultValue: "") else { return }
        guard validName(name) else {
            controller.info(title: "请输入不同的账号名", message: "名称不能重复，最多 80 个字符。")
            return
        }
        let account = AntigravityAccount(id: UUID(), name: name)
        accounts.append(account)
        AntigravityAccount.save(accounts)
        Task { await login(account) }
    }

    func renameAccount(_ account: AntigravityAccount, to name: String) {
        guard let index = accounts.firstIndex(where: { $0.id == account.id }) else { return }
        guard validName(name, excluding: account.id) else {
            PanelController.shared.info(title: "请输入不同的账号名", message: "名称不能重复，最多 80 个字符。")
            return
        }
        accounts[index] = AntigravityAccount(id: account.id, name: name, annotation: accounts[index].annotation)
        AntigravityAccount.save(accounts)
    }

    func saveAnnotations(_ account: AntigravityAccount, tags: [String], unavailable: Bool) throws {
        guard let index = accounts.firstIndex(where: { $0.id == account.id }) else { throw AnnotationError.accountChanged }
        accounts[index].annotation = try AccountAnnotations.validated(tags: tags, unavailable: unavailable)
        AntigravityAccount.save(accounts)
    }

    func credentialAccount(_ id: UUID) throws -> AntigravityAccount {
        guard !signingIn.contains(id), !DemoData.enabled else { throw AntigravityAccountActionError.busy }
        guard let account = accounts.first(where: { $0.id == id }) else { throw AnnotationError.accountChanged }
        return account
    }

    func signOut(_ id: UUID) throws {
        guard canChangeCredentials else { throw AntigravityAccountActionError.busy }
        let account = try credentialAccount(id)
        try account.signOut()
        saved[id] = nil
        if activeAccountID == id { selectActiveAccount(nil) }
        if systemSwitch?.accountID == id { cancelSystemSwitch() }
    }

    func removeAccount(_ id: UUID) throws -> URL? {
        guard canChangeCredentials else { throw AntigravityAccountActionError.busy }
        let account = try credentialAccount(id)
        let backup = try account.archive()
        accounts.removeAll { $0.id == id }
        saved[id] = nil
        if activeAccountID == id { selectActiveAccount(nil) }
        if systemSwitch?.accountID == id { cancelSystemSwitch() }
        AntigravityAccount.save(accounts)
        return backup
    }

    func login(_ account: AntigravityAccount) async {
        guard signingIn.isEmpty, !DemoData.enabled, accounts.contains(where: { $0.id == account.id }) else { return }
        signingIn.insert(account.id)
        loginCancelled = false
        loginResult = nil
        showSignInWindow()
        defer {
            if loginCancelled { loginResult = .cancelled }
            signingIn.remove(account.id)
            try? loginInput?.close()
            loginInput = nil
            loginProcess = nil
            loginURL = nil
            loginHint = nil
            submittingCode = false
        }
        do {
            let command = try await Task.detached(priority: .utility) { try AntigravityCLI.verifyIsolation() }.value
            guard !loginCancelled else { return }
            let process = Process()
            process.executableURL = FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent(".local/bin/codex-monitor")
            process.arguments = ["antigravity-login", account.id.uuidString, command.path]
            let input = Pipe()
            let output = Pipe()
            let diagnostics = Pipe()
            process.standardInput = input
            process.standardOutput = output
            process.standardError = diagnostics
            loginProcess = process
            let failure: String? = try await withCheckedThrowingContinuation { continuation in
                process.terminationHandler = { completed in
                    let data = diagnostics.fileHandleForReading.readDataToEndOfFile()
                    let message = String(decoding: data.prefix(1024), as: UTF8.self)
                    let line = message.split(separator: "\n").first ?? ""
                    let safeError = line.hasPrefix("error: ") ? String(line.dropFirst(7)) : "无法完成浏览器登录，请重试。"
                    continuation.resume(returning: completed.terminationStatus == 0 ? nil : safeError)
                }
                do {
                    try process.run()
                    loginInput = input.fileHandleForWriting
                    _ = fcntl(input.fileHandleForWriting.fileDescriptor, F_SETNOSIGPIPE, 1)
                    Task {
                        let url = await Task.detached(priority: .utility) {
                            Self.readLoginURL(output.fileHandleForReading)
                        }.value
                        guard self.loginProcess === process, process.isRunning,
                              !self.loginCancelled, let url = url else { return }
                        self.loginURL = url
                        self.clipboardVersion = NSPasteboard.general.changeCount
                        self.openLoginBrowser()
                    }
                } catch { continuation.resume(throwing: error) }
            }
            guard !loginCancelled else { return }
            if let failure = failure {
                saved[account.id, default: AntigravityAccountStatus()].error = failure
                loginResult = .failed(failure)
            } else {
                await refresh(account)
                if let eligibility = saved[account.id]?.eligibility {
                    loginResult = .verification(eligibility)
                } else {
                    loginResult = .authorized
                }
            }
        } catch {
            let reason = (error as? AntigravityCLI.Failure ?? .failed).rawValue
            saved[account.id, default: AntigravityAccountStatus()].error = reason
            loginResult = .failed(reason)
        }
    }

    func showSignInWindow() {
        guard let account = accounts.first(where: { signingIn.contains($0.id) }) else { return }
        AntigravitySignInWindow.shared.show(account: account, model: self)
    }

    nonisolated private static func readLoginURL(_ handle: FileHandle) -> URL? {
        struct Notice: Decodable { let url: String }
        var line = Data()
        while line.count < 8192 {
            guard let byte = handle.readData(ofLength: 1).first else { return nil }
            if byte == 10 {
                guard let notice = try? JSONDecoder().decode(Notice.self, from: line),
                      let parts = URLComponents(string: notice.url), parts.scheme == "https",
                      parts.host == "accounts.google.com", parts.path == "/o/oauth2/auth",
                      parts.user == nil, parts.password == nil, parts.port == nil else { return nil }
                return parts.url
            }
            line.append(byte)
        }
        return nil
    }

    func openLoginBrowser(regular: Bool = false) {
        guard let loginURL = loginURL else { return }
        if !regular, let browser = PrivateBrowser.installed, browser.open(loginURL.absoluteString) {
            return
        }
        if !NSWorkspace.shared.open(loginURL) {
            loginHint = "无法打开浏览器。请用受支持的浏览器重试。"
        }
    }

    func submitCopiedCode() {
        guard loginURL != nil, !submittingCode else { return }
        let clipboard = NSPasteboard.general
        guard clipboard.changeCount != clipboardVersion,
              let code = clipboard.string(forType: .string) else {
            loginHint = "请先在这次 Google 授权页面点击「复制到剪贴板」。"
            return
        }
        submitCode(code)
    }

    @discardableResult
    func submitCode(_ raw: String) -> Bool {
        guard loginURL != nil, loginProcess?.isRunning == true, let input = loginInput,
              !submittingCode else { return false }
        let code = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard code.range(of: #"\A4/[A-Za-z0-9._/~\-]{16,1022}\z"#, options: .regularExpression) != nil else {
            loginHint = "请使用这次 Google 授权页面给出的授权码。"
            return false
        }
        do {
            try input.write(contentsOf: Data((code + "\n").utf8))
            loginHint = nil
            submittingCode = true
            return true
        } catch {
            loginHint = "本次登录已结束，请重新登录。"
            return false
        }
    }

    func cancelLogin() {
        loginCancelled = true
        if let process = loginProcess, process.isRunning { process.terminate() }
    }

    func openLogin() {
        let workspace = NSWorkspace.shared
        if let app = workspace.urlForApplication(withBundleIdentifier: "com.google.antigravity") {
            workspace.openApplication(at: app, configuration: NSWorkspace.OpenConfiguration()) { _, failure in
                if failure != nil {
                    Task { @MainActor in self.error = "无法打开 Antigravity，请从应用程序文件夹启动。" }
                }
            }
        } else {
            workspace.open(URL(string: "https://antigravity.google/docs/install")!)
        }
    }
}
