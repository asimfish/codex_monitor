import AppKit
import Combine
import SwiftUI

// MARK: - Locating the codex binary (device-code mode only)

/// The app is started by launchd with a bare PATH, so `codex` (usually an nvm-managed npm
/// wrapper) has to be resolved explicitly. Well-known dirs first (instant), login shell as fallback.
enum CodexLocator {
    static func find() async -> String? {
        if let known = knownCandidates().first(where: isExecutableFile) { return known }
        if let fromShell = await resolveViaShell(), isExecutableFile(fromShell) { return fromShell }
        return nil
    }

    private static func isExecutableFile(_ path: String) -> Bool {
        var isDir: ObjCBool = false
        guard FileManager.default.fileExists(atPath: path, isDirectory: &isDir), !isDir.boolValue else { return false }
        return FileManager.default.isExecutableFile(atPath: path)
    }

    private static func resolveViaShell() async -> String? {
        await withCheckedContinuation { cont in
            DispatchQueue.global(qos: .userInitiated).async {
                let p = Process()
                p.executableURL = URL(fileURLWithPath: "/bin/zsh")
                p.arguments = ["-lic", "command -v codex 2>/dev/null"]
                let pipe = Pipe()
                p.standardOutput = pipe
                p.standardError = FileHandle.nullDevice
                p.standardInput = FileHandle.nullDevice
                var result: String?
                do {
                    try p.run()
                    let group = DispatchGroup()
                    group.enter()
                    p.terminationHandler = { _ in group.leave() }
                    if group.wait(timeout: .now() + 12) == .timedOut { p.terminate() }
                    let data = pipe.fileHandleForReading.readDataToEndOfFile()
                    result = (String(data: data, encoding: .utf8) ?? "")
                        .split(whereSeparator: \.isNewline)
                        .map { $0.trimmingCharacters(in: .whitespaces) }
                        .last { $0.hasPrefix("/") }
                } catch {
                    result = nil
                }
                cont.resume(returning: result)
            }
        }
    }

    private static func knownCandidates() -> [String] {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        var out: [String] = []
        let nvm = "\(home)/.nvm/versions/node"
        if let versions = try? FileManager.default.contentsOfDirectory(atPath: nvm) {
            let sorted = versions.sorted { $0.compare($1, options: .numeric) == .orderedDescending }
            out.append(contentsOf: sorted.map { "\(nvm)/\($0)/bin/codex" })
        }
        out.append(contentsOf: [
            "/opt/homebrew/bin/codex",
            "/usr/local/bin/codex",
            "\(home)/.local/bin/codex",
            "\(home)/.bun/bin/codex",
            "\(home)/.volta/bin/codex",
            "\(home)/.npm-global/bin/codex",
        ])
        return out
    }
}

// MARK: - Private-window capable browsers

struct PrivateBrowser {
    let appName: String
    let flag: String

    static let known: [PrivateBrowser] = [
        PrivateBrowser(appName: "Google Chrome", flag: "--incognito"),
        PrivateBrowser(appName: "Microsoft Edge", flag: "--inprivate"),
        PrivateBrowser(appName: "Brave Browser", flag: "--incognito"),
        PrivateBrowser(appName: "Comet", flag: "--incognito"),
        PrivateBrowser(appName: "Chromium", flag: "--incognito"),
        PrivateBrowser(appName: "Firefox", flag: "--private-window"),
    ]

    static var installed: PrivateBrowser? {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return known.first { b in
            FileManager.default.fileExists(atPath: "/Applications/\(b.appName).app")
                || FileManager.default.fileExists(atPath: "\(home)/Applications/\(b.appName).app")
        }
    }

    @discardableResult func open(_ url: String) -> Bool {
        let profile = FileManager.default.temporaryDirectory.appendingPathComponent("codex-monitor-browser-" + UUID().uuidString)
        do { try FileManager.default.createDirectory(at: profile, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700]) }
        catch { return false }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        let isolated = appName == "Firefox" ? ["--no-remote", "--profile", profile.path]
            : ["--user-data-dir=" + profile.path, "--no-first-run", "--no-default-browser-check"]
        p.arguments = ["-W", "-na", appName, "--args", flag] + isolated + [url]
        p.terminationHandler = { _ in try? FileManager.default.removeItem(at: profile) }
        do { try p.run(); return true }
        catch { try? FileManager.default.removeItem(at: profile); return false }
    }
}

// MARK: - One login run (browser OAuth by default, device code as fallback)

@MainActor
final class LoginSession: ObservableObject {
    enum Mode: String {
        case browser
        case deviceCode
    }

    enum Phase: Equatable {
        case resolving
        case starting
        case waitingBrowser(url: String)
        case waitingDevice(url: String, code: String)
        case exchanging
        case success(email: String, plan: String)
        case failed(String)
        case cancelled
    }

    @Published private(set) var phase: Phase = .resolving
    @Published private(set) var mode: Mode
    @Published private(set) var transcript = ""
    @Published private(set) var browserOpenError: String?

    let name: String
    let directory: URL

    private var process: Process?
    private var rawBuffer = ""
    private var finishedHandled = false
    private var browserFlow: BrowserLoginFlow?
    private var attempt = UUID()
    private var stagingDirectory: URL?
    private let makeBrowserFlow: @MainActor () -> BrowserLoginFlow
    private let locateCodex: () async -> String?

    init(name: String, directory: URL, mode: Mode = .browser,
         makeBrowserFlow: @escaping @MainActor () -> BrowserLoginFlow = { BrowserLoginFlow() },
         locateCodex: @escaping () async -> String? = { await CodexLocator.find() }) {
        self.name = name
        self.directory = directory
        self.mode = mode
        self.makeBrowserFlow = makeBrowserFlow
        self.locateCodex = locateCodex
    }

    var isRunning: Bool {
        switch phase {
        case .resolving, .starting, .waitingBrowser, .waitingDevice, .exchanging: return true
        default: return false
        }
    }

    /// State nonce of the current browser flow (used by the self-test to fake a callback).
    var debugState: String? { browserFlow?.state }

    func start() {
        attempt = UUID()
        browserOpenError = nil
        finishedHandled = false
        do { try ensureDirectory() }
        catch { phase = .failed(error.localizedDescription); return }
        switch mode {
        case .browser: startBrowser()
        case .deviceCode: startDeviceCode()
        }
    }

    func switchMode(_ newMode: Mode) {
        guard newMode != mode else { return }
        retry(newMode)
    }

    /// Restart from scratch in the given mode (also used from the failure screen).
    func retry(_ newMode: Mode) {
        stopEverything()
        mode = newMode
        rawBuffer = ""
        transcript = ""
        phase = .resolving
        start()
    }

    // MARK: Browser (authorization code + PKCE)

    private func ensureDirectory() throws {
        if !FileManager.default.fileExists(atPath: directory.path) {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        }
    }

    private func startBrowser() {
        phase = .starting
        do {
            try ensureDirectory()
        } catch {
            phase = .failed(error.localizedDescription)
            return
        }
        let flow = makeBrowserFlow()
        browserFlow = flow
        do {
            try flow.start { [weak self, weak flow] result in
                guard let self = self, let flow = flow, self.browserFlow === flow else { return }
                self.handleCallback(result)
            }
        } catch {
            phase = .failed(OAuthError.listener(error.localizedDescription).localizedDescription)
            browserFlow = nil
            return
        }
        phase = .waitingBrowser(url: flow.authorizeURL.absoluteString)
    }

    func handleCallback(_ result: Result<[String: String], Error>) {
        guard let flow = browserFlow, isRunning else { return }
        switch result {
        case .failure(let error):
            phase = .failed(error.localizedDescription)
            flow.stop()
            browserFlow = nil
        case .success(let query):
            guard query["state"] == flow.state else {
                // Someone else's callback; keep waiting for ours.
                return
            }
            guard let code = query["code"], !code.isEmpty else { return }
            phase = .exchanging
            let currentAttempt = attempt
            Task {
                do {
                    let tokens = try await flow.exchange(code: code)
                    guard attempt == currentAttempt, browserFlow === flow, isRunning else { return }
                    let data = try OAuthCore.buildAuthJSON(tokens)
                    try ProfileStore.shared.writeAtomically(data, to: directory.appendingPathComponent("auth.json"), createParent: false)
                    completeFromDisk()
                } catch {
                    guard attempt == currentAttempt, browserFlow === flow, isRunning else { return }
                    phase = .failed((error as? LocalizedError)?.errorDescription ?? error.localizedDescription)
                }
                flow.stop()
                browserFlow = nil
            }
        }
    }

    // MARK: Device code (spawns `codex login --device-auth`)

    private func startDeviceCode() {
        phase = .resolving
        let currentAttempt = attempt
        Task {
            let codex = await locateCodex()
            guard attempt == currentAttempt, isRunning else { return }
            guard let codex = codex else {
                phase = .failed(L.loginCodexNotFound)
                return
            }
            launchCodex(codex)
        }
    }

    private func launchCodex(_ codexPath: String) {
        phase = .starting
        do {
            try ensureDirectory()
            let staging = directory.deletingLastPathComponent().appendingPathComponent(".codex-login-" + UUID().uuidString)
            try FileManager.default.createDirectory(at: staging, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
            stagingDirectory = staging
        } catch {
            phase = .failed(error.localizedDescription)
            return
        }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: codexPath)
        p.arguments = ["login", "--device-auth"]
        var env = ProcessInfo.processInfo.environment
        env["CODEX_HOME"] = stagingDirectory!.path
        let binDir = (codexPath as NSString).deletingLastPathComponent
        env["PATH"] = "\(binDir):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
        env["NO_COLOR"] = "1"
        env["TERM"] = "dumb"
        p.environment = env
        p.currentDirectoryURL = stagingDirectory
        let currentAttempt = attempt
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        p.standardInput = FileHandle.nullDevice
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            Task { @MainActor [weak self] in
                guard let self = self, self.attempt == currentAttempt, self.isRunning else { return }
                self.consume(data)
            }
        }
        p.terminationHandler = { [weak self] proc in
            let status = proc.terminationStatus
            let staging = proc.currentDirectoryURL
            Task { @MainActor [weak self] in
                defer { if let staging = staging { try? FileManager.default.removeItem(at: staging) } }
                let rest = pipe.fileHandleForReading.readDataToEndOfFile()
                pipe.fileHandleForReading.readabilityHandler = nil
                guard let self = self, self.attempt == currentAttempt, self.isRunning else { return }
                self.consume(rest)
                self.codexFinished(status: status)
            }
        }
        do {
            try p.run()
            process = p
        } catch {
            phase = .failed(error.localizedDescription)
            if let staging = stagingDirectory { try? FileManager.default.removeItem(at: staging) }
            stagingDirectory = nil
        }
    }

    private func consume(_ data: Data) {
        guard !data.isEmpty else { return }
        let text = String(data: data, encoding: .utf8) ?? String(decoding: data, as: UTF8.self)
        rawBuffer += text
        transcript = TextScan.stripANSI(rawBuffer)
        if case .waitingDevice = phase { return }
        if let (url, code) = TextScan.deviceLogin(in: transcript) {
            phase = .waitingDevice(url: url, code: code)
        }
    }

    private func codexFinished(status: Int32) {
        guard !finishedHandled else { return }
        finishedHandled = true
        guard isRunning else { return }
        if status == 0, let staged = stagingDirectory?.appendingPathComponent("auth.json"),
           let data = try? Data(contentsOf: staged), let auth = try? JSONDecoder().decode(AuthFile.self, from: data),
           auth.hasLoginCredentials, !Identity(auth: auth).accessTokenExpired {
            do {
                try ProfileStore.shared.writeAtomically(data, to: directory.appendingPathComponent("auth.json"), createParent: false)
                completeFromDisk()
            } catch { phase = .failed(error.localizedDescription) }
        } else {
            let tail = transcript.split(whereSeparator: \.isNewline).suffix(6).joined(separator: "\n")
            phase = .failed(L.loginExited(Int(status)) + (tail.isEmpty ? "" : "\n" + tail))
        }
    }

    // MARK: Shared

    private func completeFromDisk() {
        let authURL = directory.appendingPathComponent("auth.json")
        guard let auth = try? AuthFile.load(from: authURL), auth.hasLoginCredentials else {
            phase = .failed(L.errAuthParse(authURL.path))
            return
        }
        let id = Identity(auth: auth)
        phase = .success(email: id.email ?? "?", plan: Fmt.planLabel(id.planType))
    }

    func cancel() {
        guard isRunning else { return }
        phase = .cancelled
        stopEverything()
    }

    private func stopEverything() {
        attempt = UUID()
        process?.terminate()
        process = nil
        browserFlow?.stop()
        browserFlow = nil
    }

    // MARK: Browser / clipboard helpers

    var currentURL: String? {
        switch phase {
        case .waitingBrowser(let url): return url
        case .waitingDevice(let url, _): return url
        default: return nil
        }
    }

    var currentCode: String? {
        if case .waitingDevice(_, let code) = phase { return code }
        return nil
    }

    func copyCode() {
        guard let code = currentCode else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(code, forType: .string)
    }

    func openInDefaultBrowser() {
        guard let url = currentURL, let u = URL(string: url) else { return }
        copyCode()
        NSWorkspace.shared.open(u)
    }

    func openInPrivateWindow(_ browser: PrivateBrowser) {
        guard let url = currentURL else { return }
        copyCode()
        browserOpenError = browser.open(url) ? nil : L.loginPrivateOpenFailed
    }

    static let securitySettingsURL = "https://chatgpt.com/#settings/Security"
}

// MARK: - Dev aid

/// `CODEX_MONITOR_TEST_LOGIN=browser|device` runs a login into a temp dir without user interaction:
/// device mode cancels once URL + code were parsed; browser mode fakes a callback with a bogus
/// code and expects the token exchange to be rejected. Both verify the plumbing end to end.
enum LoginDebug {
    private static var cancellable: AnyCancellable?
    private static var session: LoginSession?

    @MainActor static func runIfRequested() {
        guard let raw = ProcessInfo.processInfo.environment["CODEX_MONITOR_TEST_LOGIN"], !raw.isEmpty else { return }
        let mode: LoginSession.Mode = (raw == "device" || raw == "1") ? .deviceCode : .browser
        let dir = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("codexmonitor-logintest-\(UUID().uuidString.prefix(8))")
        let s = LoginSession(name: "logintest", directory: dir, mode: mode)
        session = s
        func done(_ note: String) {
            NSLog("[CodexMonitor][login-test] %@; dir removed = %d", note, FileManager.default.fileExists(atPath: dir.path) ? 0 : 1)
            Task { @MainActor in NSApp.terminate(nil) }
        }
        cancellable = s.$phase.sink { phase in
            switch phase {
            case .waitingDevice(let url, let code):
                NSLog("[CodexMonitor][login-test] waitingDevice url=%@ code=%@", url, String(repeating: "*", count: code.count))
                Task { @MainActor in
                    s.cancel()
                    try? await Task.sleep(nanoseconds: 800_000_000)
                    done("OK (device)")
                }
            case .waitingBrowser(let url):
                NSLog("[CodexMonitor][login-test] waitingBrowser url=%@", url.replacingOccurrences(of: #"(code_challenge|state)=[^&]+"#, with: "$1=REDACTED", options: .regularExpression))
                Task { @MainActor in
                    guard let state = s.debugState else { done("no state"); return }
                    var comps = URLComponents(string: "http://127.0.0.1:1455\(OAuthConfig.callbackPath)")!
                    comps.queryItems = [URLQueryItem(name: "code", value: "fake_code_for_selftest"), URLQueryItem(name: "state", value: state)]
                    do {
                        let (data, resp) = try await URLSession.shared.data(from: comps.url!)
                        let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
                        NSLog("[CodexMonitor][login-test] fake callback -> HTTP %d, %d bytes html", status, data.count)
                    } catch {
                        NSLog("[CodexMonitor][login-test] fake callback failed: %@", error.localizedDescription)
                        done("callback request failed")
                    }
                }
            case .exchanging:
                NSLog("[CodexMonitor][login-test] exchanging (server accepted callback, state matched)")
            case .failed(let msg):
                NSLog("[CodexMonitor][login-test] failed as expected? %@", msg)
                Task { @MainActor in
                    try? await Task.sleep(nanoseconds: 500_000_000)
                    done(mode == .browser ? "OK (browser: exchange rejected bogus code)" : "FAILED")
                }
            case .success:
                done("unexpected success")
            default:
                NSLog("[CodexMonitor][login-test] phase: %@", String(describing: phase))
            }
        }
        s.start()
        DispatchQueue.main.asyncAfter(deadline: .now() + 90) {
            NSLog("[CodexMonitor][login-test] TIMEOUT; transcript: %@", s.transcript)
            s.cancel()
            NSApp.terminate(nil)
        }
    }
}

// MARK: - Window

@MainActor
final class LoginWindowController: NSObject, NSWindowDelegate {
    static let shared = LoginWindowController()

    var hasRunningLogin: Bool { session?.isRunning == true }

    private var window: NSWindow?
    private var session: LoginSession?

    /// Ask for a profile name, then run the login in a window.
    func begin(monitor: QuotaMonitor, panel: PanelController) {
        if let s = session, s.isRunning {
            window?.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }
        guard let raw = panel.prompt(title: L.addAccountTitle, message: L.loginPromptMessage, defaultValue: "") else { return }
        let name = monitor.store.sanitise(raw)
        guard !name.isEmpty else { return }
        let dir = monitor.store.accountsRoot.appendingPathComponent(name)
        do {
            if try ProfileStore.hasStoredCredentials(in: dir) {
                panel.info(title: L.addAccountTitle, message: L.savedLoginExists(name))
                return
            }
        } catch {
            panel.info(title: L.addAccountTitle, message: error.localizedDescription)
            return
        }
        let s = LoginSession(name: name, directory: dir)
        session = s
        present(session: s, monitor: monitor)
        s.start()
    }

    /// Re-run the login for an existing profile (or the main ~/.codex login), overwriting its
    /// auth.json on success. Used when a session was revoked server-side.
    func beginRelogin(entry: QuotaMonitor.Entry, monitor: QuotaMonitor, panel: PanelController) {
        if let s = session, s.isRunning {
            window?.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }
        let ok = panel.confirm(
            title: L.reloginConfirmTitle(entry.profile.displayName),
            message: L.reloginConfirmMessage(entry.profile.authURL.path),
            okTitle: L.reloginAction
        )
        guard ok else { return }
        let name = entry.profile.isMain ? "main" : entry.profile.name
        let s = LoginSession(name: name, directory: entry.profile.directory)
        session = s
        present(session: s, monitor: monitor)
        s.start()
    }

    private func present(session: LoginSession, monitor: QuotaMonitor) {
        let view = LoginView(session: session, monitor: monitor) { [weak self] in self?.close() }
        let hosting = NSHostingView(rootView: view)
        let w = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 460, height: 380),
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        w.title = L.loginWindowTitle(session.name)
        w.contentView = hosting
        w.isReleasedWhenClosed = false
        w.delegate = self
        w.level = .floating
        w.center()
        window = w
        w.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func close() {
        session?.cancel()
        window?.orderOut(nil)
        window = nil
        session = nil
    }

    func windowWillClose(_ notification: Notification) {
        session?.cancel()
        session = nil
        window = nil
    }
}

// MARK: - SwiftUI content

struct LoginView: View {
    @ObservedObject var session: LoginSession
    @ObservedObject var monitor: QuotaMonitor
    var onClose: () -> Void
    @State private var copied = false

    private let privateBrowser = PrivateBrowser.installed

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            switch session.phase {
            case .resolving, .starting:
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text(L.loginPreparing).foregroundStyle(.secondary)
                }
            case .waitingBrowser(let url):
                browserWaitingView(url: url)
            case .waitingDevice(let url, let code):
                deviceWaitingView(url: url, code: code)
            case .exchanging:
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text(L.loginExchanging).foregroundStyle(.secondary)
                }
            case .success(let email, let plan):
                successView(email: email, plan: plan)
            case .failed(let message):
                failedView(message)
            case .cancelled:
                Text(L.loginCancelled).foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
            footer
        }
        .padding(20)
        .frame(width: 460, alignment: .topLeading)
        .frame(minHeight: 320)
    }

    private var footer: some View {
        HStack {
            if session.isRunning {
                switch session.mode {
                case .browser:
                    Button(L.loginSwitchToDevice) { session.switchMode(.deviceCode) }
                        .buttonStyle(.link)
                        .font(.caption)
                case .deviceCode:
                    Button(L.loginSwitchToBrowser) { session.switchMode(.browser) }
                        .buttonStyle(.link)
                        .font(.caption)
                }
            } else {
                Text(session.directory.path)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            Spacer()
            if session.isRunning {
                Button(L.cancel) { onClose() }
                    .keyboardShortcut(.cancelAction)
            } else {
                Button(L.loginDone) { onClose() }
                    .keyboardShortcut(.defaultAction)
            }
        }
    }

    private func openButtons() -> some View {
        HStack(spacing: 8) {
            if let b = privateBrowser {
                Button(L.loginOpenPrivate(b.appName)) { session.openInPrivateWindow(b) }
                    .buttonStyle(.borderedProminent)
            }
            Button(L.loginOpenDefault) { session.openInDefaultBrowser() }
        }
    }

    private func browserWaitingView(url: String) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(L.loginBrowserStep1).font(.headline)
            Text(L.loginBrowserHint)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            openButtons()
            if let error = session.browserOpenError { Text(error).font(.caption).foregroundStyle(.red) }
            Text(url)
                .font(.system(size: 10, design: .monospaced))
                .foregroundStyle(.secondary)
                .lineLimit(3)
                .truncationMode(.middle)
                .textSelection(.enabled)
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text(L.loginBrowserWaiting)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func deviceWaitingView(url: String, code: String) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 6) {
                Image(systemName: "info.circle.fill").foregroundStyle(.blue)
                Text(L.loginDeviceNeedsSetting)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Button(L.loginOpenSecuritySettings) {
                if let b = privateBrowser {
                    b.open(LoginSession.securitySettingsURL)
                } else if let u = URL(string: LoginSession.securitySettingsURL) {
                    NSWorkspace.shared.open(u)
                }
            }
            .font(.caption)

            Text(L.loginStep1).font(.headline)
            openButtons()
            Text(url)
                .font(.system(size: 11, design: .monospaced))
                .foregroundStyle(.secondary)
                .textSelection(.enabled)

            Text(L.loginStep2).font(.headline)
            HStack(spacing: 12) {
                Text(code)
                    .font(.system(size: 26, weight: .bold, design: .monospaced))
                    .textSelection(.enabled)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 8)
                    .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Color.primary.opacity(0.07)))
                Button(copied ? L.loginCopied : L.loginCopy) {
                    session.copyCode()
                    copied = true
                    DispatchQueue.main.asyncAfter(deadline: .now() + 2) { copied = false }
                }
            }
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text(L.loginWaiting)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func successView(email: String, plan: String) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Image(systemName: "checkmark.circle.fill").foregroundStyle(.green).font(.title2)
                VStack(alignment: .leading, spacing: 2) {
                    Text(L.loginSuccessTitle).font(.headline)
                    Text("\(email)  \(plan)").foregroundStyle(.secondary)
                }
            }
            Text(L.loginSuccessHint(session.name))
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Button(L.loginMakeActive) {
                monitor.reloadProfiles()
                monitor.activate(entryId: session.name)
                onClose()
            }
            .buttonStyle(.borderedProminent)
        }
        .onAppear {
            monitor.reloadProfiles()
            Task { await monitor.refreshAll() }
        }
    }

    private func failedView(_ message: String) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: "xmark.octagon.fill").foregroundStyle(.red).font(.title2)
                Text(L.loginFailedTitle).font(.headline)
            }
            Text(message)
                .font(.system(size: 11, design: .monospaced))
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 8) {
                Button(L.loginRetryBrowser) { session.retry(.browser) }
                Button(L.loginRetryDevice) { session.retry(.deviceCode) }
            }
            Text(L.loginFallbackHint(session.name))
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
