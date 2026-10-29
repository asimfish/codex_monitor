import AppKit
import SwiftUI

struct ApiCreditAccount: Decodable, Identifiable {
    let id: String
    let label: String
    let organization: String
    let grantTotal: String?
    let periodMode: String
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
        } catch {
            let message = error.localizedDescription
            if message.contains("invalid or expired") { self.error = "Admin Key 无效或已过期，请到官方 Admin Keys 页面重新创建。" }
            else if message.contains("(403)") { self.error = "费用查询权限不足（403），请确认使用 Admin Key、组织正确且具备 Owner 权限。" }
            else if message.contains("does not match") { self.error = "返回的组织 ID 与所选组织不一致。请清空高级设置中的组织 ID，再按 Key 所属组织验证；本次未保存。" }
            else if message.contains("ID was not returned") { self.error = "Key 已验证，但未返回组织 ID。请展开高级设置，填写官方设置中的 org-… ID 后重试。" }
            else { self.error = message }
        }
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
                                metric(account.periodMode == "calendar_month" ? "本月已报告费用" : "已报告费用", apiMoney(account.costs?.costUsd))
                                metric("估算剩余", apiMoney(account.estimatedRemaining))
                            }
                            Text("\(account.periodMode == "calendar_month" ? "费用统计月份（UTC）" : "赠送到期")：\(account.periodMode == "calendar_month" ? String(account.periodStart.prefix(7)) : apiDate(account.periodEnd))\n最近同步：\(apiDate(account.lastSuccess))　下次刷新：\(apiDate(account.nextRefresh))")
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
            Button("用普通浏览器打开官方账单", action: { Task { await model.run("open-settings", body: ["page": "billing"]) } }).buttonStyle(.link)
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
    @State private var hasGrant = false
    @State private var manual = false
    @State private var advanced = false
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(account == nil ? "添加 API 账号" : "编辑 API 账号").font(.title2.bold())
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("两步完成配置").font(.headline)
                        Text("1. 在官方后台创建 Admin Key。\n2. 粘贴到这里，点击「验证并添加」。").font(.callout)
                        Button("用普通浏览器打开 Admin Keys 创建页面 ↗") { Task { await model.run("open-settings", body: ["page": "admin_keys"]) } }.buttonStyle(.link)
                        Text("先切换到领取额度的 API 组织；需要该组织的 Owner（所有者）权限。").font(.caption).foregroundStyle(.secondary)
                    }.padding(14).frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 10))
                    SecureField("Admin Key（sk-admin-...）", text: $key)
                    Text("只保存在本机；编辑时留空保留原 Key。普通项目 Key 无法查询费用。").font(.caption).foregroundStyle(.secondary)
                    TextField("账号名称（可选）", text: $label)
                    Toggle("暂不配置 Admin Key，仅保存手工记录", isOn: $manual).toggleStyle(.checkbox)
                        .onChange(of: manual) { if manual { advanced = true } }
                    Toggle("补充赠送金额和实际有效期（可之后再填）", isOn: $hasGrant).toggleStyle(.checkbox)
                    if hasGrant {
                        TextField("赠送总额 USD（未知时留空）", text: $total)
                        DatePicker("赠送开始", selection: $start)
                        DatePicker("赠送到期", selection: $end)
                        Text("照官方账单填写实际到账金额和时间；剩余额度仅为估算，到期不会自动续发。").font(.caption).foregroundStyle(.secondary)
                    } else {
                        Text("先同步本自然月费用（UTC）；补充实际赠送金额和有效期后，才会估算余额。").font(.caption).foregroundStyle(.secondary)
                    }
                    DisclosureGroup("高级设置 · 组织和刷新间隔", isExpanded: $advanced) {
                        VStack(alignment: .leading, spacing: 12) {
                            TextField("组织 ID（通常自动识别，org-...）", text: $organization)
                            Text("留空时从成功的费用响应识别；手工记录需要填写组织 ID。").font(.caption).foregroundStyle(.secondary)
                            Button("用普通浏览器查看组织 ID ↗") { Task { await model.run("open-settings", body: ["page": "organization"]) } }.buttonStyle(.link)
                            Stepper("刷新间隔：\(interval) 秒", value: $interval, in: 60...3600, step: 60)
                        }.padding(.top, 10)
                    }
                    if let error = model.error { Text(error).foregroundStyle(.red).font(.caption).textSelection(.enabled) }
                }.padding(.trailing, 8)
            }
            HStack {
                Button("取消") { key = ""; dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                if model.busy { ProgressView().controlSize(.small) }
                Button(model.busy ? "正在验证…" : (!key.isEmpty || (account == nil && !manual) ? (account == nil ? "验证并添加" : "验证并保存") : "保存")) {
                    var body: [String: Any] = ["label": label, "organization": organization.trimmingCharacters(in: .whitespacesAndNewlines),
                                             "admin_key": key.trimmingCharacters(in: .whitespacesAndNewlines), "period_mode": hasGrant ? "custom" : "calendar_month", "refresh_interval": interval]
                    if hasGrant { body["grant_total"] = total; body["period_start"] = ISO8601.format(start); body["period_end"] = ISO8601.format(end) }
                    if let account = account { body["id"] = account.id }
                    Task {
                        await model.run(key.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "save" : "connect", body: body)
                        if model.error == nil { key = ""; dismiss() }
                        else if model.error?.contains("org-") == true || model.error?.contains("组织 ID") == true { advanced = true }
                    }
                }.keyboardShortcut(.defaultAction)
                    .disabled(account == nil && !manual && key.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }.padding(24).frame(width: 510, height: 600).disabled(model.busy)
        .onAppear {
            model.error = nil
            label = account?.label ?? ""; organization = account.flatMap { $0.organization.hasPrefix("org-") ? $0.organization : nil } ?? ""; total = account?.grantTotal ?? ""
            if let account = account {
                start = ISO8601.parse(account.periodStart) ?? Date()
                end = ISO8601.parse(account.periodEnd) ?? Date()
                interval = account.refreshInterval
                hasGrant = account.periodMode == "custom"
                manual = !account.hasAdminKey
                advanced = manual
            }
        }
        .onDisappear { key = "" }
    }
}
