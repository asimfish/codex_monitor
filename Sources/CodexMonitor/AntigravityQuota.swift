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
    enum Failure: String, Error {
        case missing = "安装官方 Antigravity CLI（agy）后即可同步额度。"
        case version = "请将 Antigravity CLI 更新到 1.1.11 或更新版本。"
        case login = "请登录 Antigravity，然后点刷新。"
        case unavailable = "当前登录方式没有返回可显示的模型额度。"
        case failed = "额度读取失败，请检查 Antigravity 登录状态和网络后刷新。"
    }

    private static func executable() throws -> URL {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let paths = [home.appendingPathComponent(".local/bin/agy"),
                     URL(fileURLWithPath: "/opt/homebrew/bin/agy"),
                     URL(fileURLWithPath: "/usr/local/bin/agy")]
        guard let command = paths.first(where: { FileManager.default.isExecutableFile(atPath: $0.path) }) else {
            throw Failure.missing
        }
        return command
    }

    static func supportsQuota(_ version: String) -> Bool {
        let numbers = version.trimmingCharacters(in: .whitespacesAndNewlines)
            .split(separator: ".", omittingEmptySubsequences: false)
        guard numbers.count == 3, let major = Int(numbers[0]), let minor = Int(numbers[1]),
              let patch = Int(numbers[2]), min(major, minor, patch) >= 0 else { return false }
        // v1.1.11 introduced read-only /usage in print mode. Older versions could submit it as a prompt.
        return major > 1 || (major == 1 && (minor > 1 || (minor == 1 && patch >= 11)))
    }

    static func readQuota() throws -> AntigravityQuota {
        let command = try executable()
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("codex-monitor-agy-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        defer { try? FileManager.default.removeItem(at: directory) }
        let version = try run(command, ["--version"], in: directory)
        guard version.status == 0, supportsQuota(String(decoding: version.output, as: UTF8.self)) else {
            throw Failure.version
        }
        let result = try run(command, ["-p", "/usage", "--output-format", "json", "--print-timeout", "15s"], in: directory)
        if result.status != 0 {
            let message = String(decoding: result.output + result.diagnostics, as: UTF8.self).lowercased()
            if message.contains("authentication required") || message.contains("not authenticated") {
                throw Failure.login
            }
            throw Failure.failed
        }
        guard let quota = AntigravityQuota.decode(result.output), !quota.groups.isEmpty,
              quota.groups.contains(where: { !$0.buckets.isEmpty }) else { throw Failure.unavailable }
        return quota
    }

    private static func run(_ executable: URL, _ arguments: [String], in directory: URL)
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
        process.environment = ProcessInfo.processInfo.environment.filter { allowedEnvironment.contains($0.key) }
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
    @Published private(set) var busy = false
    private var polling: Task<Void, Never>?

    func start() {
        guard polling == nil, !DemoData.enabled else { return }
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
        do {
            let quota = try await Task.detached(priority: .utility) { try AntigravityCLI.readQuota() }.value
            groups = quota.groups.filter { !$0.buckets.isEmpty }
            lastSync = Date()
            error = nil
        } catch {
            // The official command follows the active login. Avoid showing a prior account after a failed read.
            groups = []
            lastSync = nil
            self.error = (error as? AntigravityCLI.Failure ?? .failed).rawValue
        }
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
                if let error = model.error {
                    Text(error).font(.caption2).foregroundStyle(.secondary)
                } else if model.groups.isEmpty {
                    Text(model.busy ? "正在读取额度…" : "登录 Antigravity 后可查看额度。")
                        .font(.caption2).foregroundStyle(.secondary)
                }
                ForEach(Array(model.groups.enumerated()), id: \.offset) { _, group in
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
                if let lastSync = model.lastSync {
                    Text("当前登录 · 同步 \(Fmt.dateTime.string(from: lastSync))")
                        .font(.caption2).foregroundStyle(.secondary)
                }
            }
        }.accessibilityIdentifier("antigravity-section")
    }
}
