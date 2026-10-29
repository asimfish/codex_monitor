import AppKit
import SwiftUI

struct ApiCreditAccount: Decodable, Identifiable {
    let id: String
    let label: String
    let organization: String
    let grantTotal: String?
    let periodStart: String
    let periodEnd: String
    let refreshInterval: Int
    let lastSuccess: String?
    let nextRefresh: String?
    let estimatedRemaining: String?
    let error: String?
    let hasAdminKey: Bool
    let expired: Bool
    let stale: Bool
    let costs: ApiReportedCosts?
}

struct ApiReportedCosts: Decodable {
    let costUsd: String
}

struct ApiCreditSnapshot: Decodable {
    let accounts: [ApiCreditAccount]
}

@MainActor
final class ApiCreditModel: ObservableObject {
    private let command: URL
    init(command: URL = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".local/bin/codex-monitor")) {
        self.command = command
    }
    @Published var accounts: [ApiCreditAccount] = []
    @Published var busy = false
    @Published var error: String?

    func run(_ action: String = "list", id: String? = nil, body: [String: Any]? = nil) async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        let command = self.command
        let result: Result<Data, Error> = await withCheckedContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                let process = Process()
                guard FileManager.default.isExecutableFile(atPath: command.path) else {
                    continuation.resume(returning: .failure(NSError(domain: "ApiCredits", code: 1,
                        userInfo: [NSLocalizedDescriptionKey: "找不到 codex-monitor 命令，请按 README 重新运行原生安装脚本。"])))
                    return
                }
                process.executableURL = command
                process.arguments = ["api-credits", action] + (id.map { ["--id", $0] } ?? [])
                process.environment = ProcessInfo.processInfo.environment
                let output = Pipe(), input = Pipe()
                process.standardOutput = output
                process.standardError = output
                process.standardInput = input
                do {
                    let data = try body.map { try JSONSerialization.data(withJSONObject: $0) }
                    try process.run()
                    DispatchQueue.global().asyncAfter(deadline: .now() + 90) {
                        if process.isRunning { process.terminate() }
                    }
                    if let data = data { input.fileHandleForWriting.write(data) }
                    try? input.fileHandleForWriting.close()
                    let response = output.fileHandleForReading.readDataToEndOfFile()
                    process.waitUntilExit()
                    guard process.terminationStatus == 0 else {
                        throw NSError(domain: "ApiCredits", code: Int(process.terminationStatus),
                                      userInfo: [NSLocalizedDescriptionKey: String(data: response, encoding: .utf8) ?? "API 额度操作失败"])
                    }
                    continuation.resume(returning: .success(response))
                } catch { continuation.resume(returning: .failure(error)) }
            }
        }
        do {
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            accounts = try decoder.decode(ApiCreditSnapshot.self, from: result.get()).accounts
            error = nil
        } catch { self.error = error.localizedDescription }
    }
}

@MainActor
final class ApiCreditsController {
    static let shared = ApiCreditsController()
    private var window: NSWindow?

    func previewIfRequested() {
        guard DemoData.enabled, let path = ProcessInfo.processInfo.environment["CODEX_MONITOR_API_SNAPSHOT"] else { return }
        open()
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) {
            guard let view = self.window?.contentView,
                  let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
            view.cacheDisplay(in: view.bounds, to: bitmap)
            if let png = bitmap.representation(using: .png, properties: [:]) { try? png.write(to: URL(fileURLWithPath: path)) }
            NSApp.terminate(nil)
        }
    }
    func open() {
        if let window = window {
            window.makeKeyAndOrderFront(nil)
        } else {
            let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 740, height: 640),
                                  styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
            window.title = "OpenAI API 额度"
            let hosting = NSHostingView(rootView: ApiCreditsView())
            hosting.sizingOptions = []
            window.contentView = hosting
            window.isReleasedWhenClosed = false
            window.minSize = NSSize(width: 530, height: 400)
            window.center()
            window.makeKeyAndOrderFront(nil)
            self.window = window
        }
        NSApp.activate(ignoringOtherApps: true)
    }
}

private func apiMoney(_ value: String?) -> String {
    guard let value = value, let decimal = Decimal(string: value) else { return "—" }
    return NSDecimalNumber(decimal: decimal).doubleValue.formatted(.currency(code: "USD"))
}

private func apiDate(_ value: String?) -> String {
    value.flatMap(ISO8601.parse).map { Fmt.dateTime.string(from: $0) } ?? "—"
}

private struct ApiCreditsView: View {
    @StateObject private var model = ApiCreditModel()
    @State private var editing: ApiCreditAccount?
    @State private var showEditor = false
    @State private var removing: ApiCreditAccount?
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("API 额度总览").font(.title2.bold())
                Spacer()
                if model.busy { ProgressView().controlSize(.small) }
                Button("刷新费用") { Task { await model.run("refresh") } }
                Button("添加 API 账号") { editing = nil; showEditor = true }
            }
            Text("总额和账期需照官方账单填写；Admin Key 自动查询组织费用。估算剩余不是官方余额，可能受折扣、其他赠送和统计延迟影响。到期后请更新实际到账的新额度。")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if let error = model.error { Text(error).font(.caption).foregroundStyle(.red).textSelection(.enabled) }
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 14) {
                    if model.accounts.isEmpty { Text("暂无 API 组织，点击上方添加。普通项目 Key 无法读取组织费用，可先不填 Key 保存额度记录。").foregroundStyle(.secondary).padding(.vertical) }
                    ForEach(model.accounts) { account in
                        VStack(alignment: .leading, spacing: 10) {
                            HStack {
                                VStack(alignment: .leading) {
                                    Text(account.label).font(.headline)
                                    Text(account.organization).font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer()
                                Button("编辑") { editing = account; showEditor = true }
                                Button("移除", role: .destructive) { removing = account }
                            }
                            HStack(spacing: 25) {
                                metric("录入总额", apiMoney(account.grantTotal))
                                metric("已报告费用", apiMoney(account.costs?.costUsd))
                                metric("估算剩余", apiMoney(account.estimatedRemaining))
                            }
                            Text("到期：\(apiDate(account.periodEnd))\n最近同步：\(apiDate(account.lastSuccess))　下次刷新：\(apiDate(account.nextRefresh))")
                                .font(.caption).foregroundStyle(.secondary)
                            if account.expired { Text("赠送已到期，尚未录入新额度").font(.caption).foregroundStyle(.orange) }
                            else if !account.hasAdminKey { Text("未配置 Admin Key，费用未知").font(.caption).foregroundStyle(.secondary) }
                            else if account.stale { Text("尚未同步或当前为旧数据").font(.caption).foregroundStyle(.orange) }
                            if let error = account.error { Text(error).font(.caption).foregroundStyle(.red) }
                        }
                        .padding(16).frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 12))
                    }
                }
            }
            Link("打开官方账单核对余额", destination: URL(string: "https://platform.openai.com/settings/organization/billing/overview")!)
                .font(.caption)
        }
        .padding(20).background(Color(nsColor: .windowBackgroundColor)).disabled(model.busy)
        .task {
            await model.run("poll")
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 30_000_000_000)
                if !Task.isCancelled { await model.run("poll") }
            }
        }
        .sheet(isPresented: $showEditor) { ApiCreditEditor(account: editing, model: model) }
        .alert("移除此 API 记录和保存的 Admin Key？", isPresented: Binding(get: { removing != nil }, set: { if !$0 { removing = nil } })) {
            Button("取消", role: .cancel) { removing = nil }
            Button("移除", role: .destructive) {
                let id = removing?.id
                removing = nil
                if let id = id { Task { await model.run("remove", id: id) } }
            }
        } message: { Text("现有 Codex 登录不会改变。") }
    }
    private func metric(_ name: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 4) { Text(name).font(.caption).foregroundStyle(.secondary); Text(value).font(.title3.monospacedDigit().bold()) }
    }
}

private struct ApiCreditEditor: View {
    let account: ApiCreditAccount?
    @ObservedObject var model: ApiCreditModel
    @Environment(\.dismiss) private var dismiss
    @State private var label = ""
    @State private var organization = ""
    @State private var key = ""
    @State private var total = ""
    @State private var start = Date()
    @State private var end = Date()
    @State private var interval = 300
    var body: some View {
        Form {
            Text("API 账号").font(.title2.bold())
            TextField("账号名称", text: $label)
            TextField("组织 ID（org-...）", text: $organization)
            SecureField("Admin Key（可选，sk-admin-...）", text: $key)
            Text("仅存本机；编辑时留空保留原 Key。普通项目 Key 无法查询费用。").font(.caption).foregroundStyle(.secondary)
            TextField("赠送总额 USD（可留空）", text: $total)
            DatePicker("赠送开始", selection: $start)
            DatePicker("赠送到期", selection: $end)
            Stepper("刷新间隔：\(interval) 秒", value: $interval, in: 60...3600, step: 60)
            Text("请照官方账单填写实际到账金额和时间。到期不自动续发，剩余额度仅为估算。").font(.caption).foregroundStyle(.secondary)
            if let error = model.error { Text(error).foregroundStyle(.red).font(.caption) }
            HStack {
                Button("取消") { key = ""; dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button("保存") {
                    var body: [String: Any] = ["label": label, "organization": organization.trimmingCharacters(in: .whitespacesAndNewlines),
                                             "admin_key": key, "grant_total": total, "period_start": ISO8601.format(start),
                                             "period_end": ISO8601.format(end), "refresh_interval": interval]
                    if let account = account { body["id"] = account.id }
                    Task { await model.run("save", body: body); if model.error == nil { key = ""; dismiss() } }
                }.keyboardShortcut(.defaultAction)
            }
        }.padding(24).frame(width: 510).disabled(model.busy)
        .onAppear {
            label = account?.label ?? ""; organization = account?.organization ?? ""; total = account?.grantTotal ?? ""
            if let account = account {
                start = ISO8601.parse(account.periodStart) ?? Date()
                end = ISO8601.parse(account.periodEnd) ?? Date()
                interval = account.refreshInterval
            }
        }
        .onDisappear { key = "" }
    }
}
