import AppKit
import SwiftUI

struct AntigravityQuota: Decodable {
    struct Group: Decodable {
        let name: String
        let buckets: [Bucket]
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

        var resetDate: Date? {
            resetTime.flatMap(ISO8601.parse).flatMap { $0 > Date() ? $0 : nil }
        }
    }

    let groups: [Group]

    static func tightestGeminiBucket(in groups: [Group]) -> Bucket? {
        groups.filter { $0.name.localizedCaseInsensitiveContains("Gemini") }
            .flatMap(\.buckets)
            .compactMap { bucket -> (Bucket, Double)? in
                guard bucket.disabled != true, let fraction = bucket.fraction else { return nil }
                return (bucket, fraction)
            }
            .min { $0.1 < $1.1 }?.0
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
        return try query(command, profile: profile, in: directory)
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

    private static func query(_ command: URL, profile: URL?, in directory: URL) throws -> AntigravityQuota {
        let profileArguments = profile.map { ["--gemini_dir", $0.path, "--app_data_dir", "antigravity-cli"] } ?? []
        let arguments = profileArguments + ["-p", "/usage", "--output-format", "json", "--print-timeout", "15s"]
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

    static func readSystemEmail() -> String? {
        struct Identity: Decodable { let email: String? }
        guard let command = try? executable() else { return nil }
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("codex-monitor-agy-" + UUID().uuidString)
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
                                                    attributes: [.posixPermissions: 0o700])
            defer { try? FileManager.default.removeItem(at: directory) }
            let helper = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".local/bin/codex-monitor")
            let result = try run(helper, ["antigravity-identity", command.path], in: directory)
            guard result.status == 0 else { return nil }
            return try JSONDecoder().decode(Identity.self, from: result.output).email
        } catch {
            return nil
        }
    }

    private static func run(_ executable: URL, _ arguments: [String], in directory: URL, isolated: Bool = false)
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
        DispatchQueue.global().asyncAfter(deadline: .now() + 25, execute: timeout)
        DispatchQueue.global().asyncAfter(deadline: .now() + 27, execute: kill)
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
    static let shared = AntigravityModel()
    @Published private(set) var groups: [AntigravityQuota.Group] = []
    @Published private(set) var lastSync: Date?
    @Published private(set) var error: String?
    @Published private(set) var systemEmail: String?
    @Published private(set) var systemEligibility: AntigravityCLI.Eligibility?
    @Published private(set) var busy = false
    @Published private(set) var accounts = AntigravityAccount.load()
    @Published private(set) var saved: [UUID: AntigravityAccountStatus] = [:]
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

    func start() {
        guard polling == nil else { return }
        if DemoData.enabled {
            groups = DemoData.antigravityGroups()
            systemEmail = "gemini@example.com"
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
        let identity = Task.detached(priority: .utility) { AntigravityCLI.readSystemEmail() }
        do {
            let quota = try await Task.detached(priority: .utility) { try AntigravityCLI.readQuota() }.value
            groups = quota.groups.filter { !$0.buckets.isEmpty }
            lastSync = Date()
            error = nil
            systemEligibility = nil
        } catch {
            // The official command follows the active login. Avoid showing a prior account after a failed read.
            groups = []
            lastSync = nil
            systemEligibility = error as? AntigravityCLI.Eligibility
            self.error = systemEligibility == nil ? (error as? AntigravityCLI.Failure ?? .failed).rawValue : nil
        }
        systemEmail = await identity.value
        for account in accounts where !signingIn.contains(account.id) {
            await refresh(account)
        }
    }

    private func refresh(_ account: AntigravityAccount) async {
        guard account.hasToken else {
            saved[account.id] = nil
            return
        }
        do {
            let quota = try await Task.detached(priority: .utility) {
                try AntigravityCLI.readQuota(profile: account.directory)
            }.value
            saved[account.id] = AntigravityAccountStatus(groups: quota.groups.filter { !$0.buckets.isEmpty }, sync: Date())
        } catch {
            if let eligibility = error as? AntigravityCLI.Eligibility {
                saved[account.id] = AntigravityAccountStatus(eligibility: eligibility)
            } else {
                saved[account.id] = AntigravityAccountStatus(error: (error as? AntigravityCLI.Failure ?? .failed).rawValue)
            }
        }
    }

    func refreshSavedAccount(_ account: AntigravityAccount) async {
        guard !busy, !signingIn.contains(account.id), !DemoData.enabled else { return }
        busy = true
        defer { busy = false }
        await refresh(account)
    }

    func addAccount() {
        let controller = PanelController.shared
        guard let name = controller.prompt(title: "添加 Antigravity 账号",
                                           message: "给账号起个名字。完成 Google 授权后，在 Antigravity 登录窗口提交授权码。",
                                           defaultValue: "") else { return }
        guard name.count <= 80, !name.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains),
              !accounts.contains(where: { $0.name.caseInsensitiveCompare(name) == .orderedSame }) else {
            controller.info(title: "请输入不同的账号名", message: "名称不能重复，最多 80 个字符。")
            return
        }
        let account = AntigravityAccount(id: UUID(), name: name)
        accounts.append(account)
        AntigravityAccount.save(accounts)
        Task { await login(account) }
    }

    func login(_ account: AntigravityAccount) async {
        guard signingIn.isEmpty, !DemoData.enabled else { return }
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
                saved[account.id] = AntigravityAccountStatus(error: failure)
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
            saved[account.id] = AntigravityAccountStatus(error: reason)
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

struct AntigravityWidgetSection: View {
    @ObservedObject var model: AntigravityModel
    @AppStorage("antigravityExpanded") private var expanded = true

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Button { expanded.toggle() } label: {
                    Label("Antigravity", systemImage: expanded ? "chevron.down" : "chevron.right")
                        .font(.caption.weight(.semibold))
                }.buttonStyle(.plain)
                Spacer()
                Button("登录 / 切换") { model.openLogin() }.buttonStyle(.link).font(.caption)
                Button { Task { await model.refresh() } } label: {
                    if model.busy { ProgressView().controlSize(.mini) }
                    else { Image(systemName: "arrow.clockwise") }
                }.buttonStyle(.plain).disabled(model.busy).help("刷新 Antigravity 额度")
            }.padding(.horizontal, 4)
            if expanded {
                HStack {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(model.systemEmail ?? "系统账号").font(.system(size: 12, weight: .semibold)).lineLimit(1)
                        Text("当前系统登录").font(.caption2).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("添加账号") { model.addAccount() }
                        .buttonStyle(.link).font(.caption)
                        .disabled(model.busy || !model.signingIn.isEmpty || DemoData.enabled)
                }.padding(.horizontal, 4)
                if let eligibility = model.systemEligibility {
                    AntigravityVerificationNotice(eligibility: eligibility)
                } else if let error = model.error {
                    Text(error).font(.caption2).foregroundStyle(.secondary)
                } else if model.groups.isEmpty {
                    Text(model.busy ? "正在读取额度…" : "登录 Antigravity 后可查看额度。")
                        .font(.caption2).foregroundStyle(.secondary)
                }
                quotaGroups(model.groups)
                if let lastSync = model.lastSync {
                    Text("同步 \(Fmt.dateTime.string(from: lastSync))")
                        .font(.caption2).foregroundStyle(.secondary)
                }
                ForEach(model.accounts) { account in
                    savedAccount(account)
                }
            }
        }.accessibilityIdentifier("antigravity-section")
    }

    private func savedAccount(_ account: AntigravityAccount) -> some View {
        let status = model.saved[account.id]
        let signingIn = model.signingIn.contains(account.id)
        return VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(account.name).font(.system(size: 12, weight: .semibold)).lineLimit(1)
                Spacer()
                if account.hasToken && !signingIn {
                    Button { Task { await model.refreshSavedAccount(account) } } label: {
                        Image(systemName: "arrow.clockwise")
                    }.buttonStyle(.plain).help("刷新此账号额度").disabled(model.busy)
                }
                Button(signingIn ? "打开登录窗口" : (account.hasToken ? "重新登录" : "登录")) {
                    if signingIn { model.showSignInWindow() }
                    else { Task { await model.login(account) } }
                }.buttonStyle(.link).font(.caption)
                    .disabled(!signingIn && (model.busy || !model.signingIn.isEmpty))
            }
            if signingIn {
                Text(model.submittingCode ? "正在核验授权…" : "请在 Antigravity 登录窗口提交授权码。")
                    .font(.caption2).foregroundStyle(.secondary)
            } else if let eligibility = status?.eligibility {
                AntigravityVerificationNotice(eligibility: eligibility)
            } else if let error = status?.error {
                Text(error).font(.caption2).foregroundStyle(.secondary)
            } else if let status = status, let sync = status.sync {
                quotaGroups(status.groups)
                Text("同步 \(Fmt.dateTime.string(from: sync))").font(.caption2).foregroundStyle(.secondary)
            } else {
                Text(account.hasToken ? "等待同步" : "尚未登录").font(.caption2).foregroundStyle(.secondary)
            }
        }.padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.primary.opacity(0.05), in: RoundedRectangle(cornerRadius: 10))
    }

    private func quotaGroups(_ groups: [AntigravityQuota.Group]) -> some View {
        ForEach(Array(groups.enumerated()), id: \.offset) { _, group in
            VStack(alignment: .leading, spacing: 6) {
                Text(group.name).font(.system(size: 12, weight: .semibold))
                ForEach(Array(group.buckets.enumerated()), id: \.offset) { _, bucket in
                    HStack(spacing: 8) {
                        Text(bucket.title)
                        Spacer()
                        if bucket.disabled == true {
                            Text("已停用").foregroundStyle(.secondary)
                        } else {
                            if let date = bucket.resetDate {
                                Text("重置 \(Fmt.dateTime.string(from: date))").foregroundStyle(.secondary)
                            }
                            Text(bucket.fraction.map { "剩余 " + $0.formatted(.percent.precision(.fractionLength(0))) } ?? "—")
                                .fontWeight(.semibold).monospacedDigit()
                        }
                    }.font(.caption)
                }
            }
            .padding(10).frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.primary.opacity(0.05), in: RoundedRectangle(cornerRadius: 10))
        }
    }
}
