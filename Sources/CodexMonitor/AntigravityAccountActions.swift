import AppKit
import SwiftUI

enum AntigravityAccountActionError: LocalizedError {
    case busy, unreadable, clipboard

    var errorDescription: String? {
        switch self {
        case .busy: return "账号正在同步或登录，请稍后重试。"
        case .unreadable: return "凭证无法复制为文本。可使用导出保存原始文件。"
        case .clipboard: return "无法写入剪贴板。"
        }
    }
}

struct AntigravityAccountMenu: View {
    let account: AntigravityAccount
    let hasToken: Bool
    @ObservedObject var model: AntigravityModel

    var body: some View {
        let signingIn = model.signingIn.contains(account.id)
        Menu {
            Button(L.renameAccount) { rename() }
            Button(L.editTags) { Self.editTags(account, model: model) }
            if model.accounts.count > 1 && account.id != model.activeAccount?.id {
                Menu(L.reorderAccounts) {
                    Button(L.moveAccountFirst) { model.moveAccount(account, direction: .first) }
                    Button(L.moveAccountUp) { model.moveAccount(account, direction: .up) }
                    Button(L.moveAccountDown) { model.moveAccount(account, direction: .down) }
                    Button(L.moveAccountLast) { model.moveAccount(account, direction: .last) }
                    if !model.usesAutomaticAccountOrder {
                        Divider()
                        Button("恢复自动排序") { model.restoreAutomaticAccountOrder() }
                    }
                }
            }
            if hasToken && !signingIn {
                Divider()
                Button("切换官方登录…") { model.beginSystemSwitch(account) }
                    .disabled(DemoData.enabled)
                Divider()
                Button("复制登录凭证") { copyCredential() }
                Button("复制凭证路径") { copyPath() }
                Button(L.revealInFinder) { revealCredential() }
                Button("导出登录凭证…") { exportCredential() }
                Divider()
                Button(L.logoutAction, role: .destructive) { signOut() }
                    .disabled(!model.canChangeCredentials)
            }
            Divider()
            Button(L.deleteAccount, role: .destructive) { remove() }
                .disabled(!model.canChangeCredentials)
        } label: {
            Image(systemName: "ellipsis.circle").font(.caption)
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .help(L.accountActionsHelp)
        .accessibilityLabel(L.accountActionsHelp)
    }

    @MainActor
    static func editTags(_ account: AntigravityAccount, model: AntigravityModel) {
        TagEditorController.shared.open(id: "antigravity:" + account.id.uuidString, name: account.name,
                                        annotation: account.annotation ?? AccountAnnotation(),
                                        hint: "标签保存在本机，刷新、重启和重新登录后保留。") { tags, unavailable in
            try model.saveAnnotations(account, tags: tags, unavailable: unavailable)
        }
    }

    @MainActor private func rename() {
        if let name = PanelController.shared.prompt(title: L.renameAccount,
                                                    message: "输入新的本地名称，最多 80 个字符。登录、标签和排序会保留。",
                                                    defaultValue: account.name) {
            model.renameAccount(account, to: name)
        }
    }

    @MainActor private func copyCredential() {
        perform("复制登录凭证") {
            let data = try model.credentialAccount(account.id).readCredential()
            guard let text = String(data: data, encoding: .utf8) else { throw AntigravityAccountActionError.unreadable }
            let item = NSPasteboardItem()
            item.setString(text, forType: .string)
            item.setData(Data(), forType: .init("org.nspasteboard.ConcealedType"))
            NSPasteboard.general.clearContents()
            guard NSPasteboard.general.writeObjects([item]) else { throw AntigravityAccountActionError.clipboard }
            QuotaMonitor.shared.note("已复制 Antigravity 登录凭证 · \(account.name)")
        }
    }

    @MainActor private func copyPath() {
        perform("复制凭证路径") {
            let url = try model.credentialAccount(account.id).tokenURL
            NSPasteboard.general.clearContents()
            guard NSPasteboard.general.setString(url.path, forType: .string) else { throw AntigravityAccountActionError.clipboard }
            QuotaMonitor.shared.note("已复制 Antigravity 凭证路径 · \(account.name)")
        }
    }

    @MainActor private func revealCredential() {
        perform(L.revealInFinder) {
            let profile = try model.credentialAccount(account.id)
            guard profile.hasToken else { throw CocoaError(.fileReadNoSuchFile) }
            NSWorkspace.shared.activateFileViewerSelecting([profile.tokenURL])
        }
    }

    @MainActor private func exportCredential() {
        let panel = NSSavePanel()
        panel.title = "导出 Antigravity 登录凭证"
        panel.nameFieldStringValue = AntigravityAccount.tokenName
        panel.canCreateDirectories = true
        NSApp.activate(ignoringOtherApps: true)
        guard panel.runModal() == .OK, let url = panel.url else { return }
        perform("导出登录凭证") {
            try model.credentialAccount(account.id).exportCredential(to: url)
            QuotaMonitor.shared.note("已导出 Antigravity 登录凭证 · \(account.name)")
        }
    }

    @MainActor private func signOut() {
        guard PanelController.shared.confirm(title: L.logoutTitle(account.name),
                                             message: "清除此独立账号在本机的登录凭证。账号、标签和排序会保留，可随时重新登录。",
                                             okTitle: L.logoutAction, destructive: true) else { return }
        perform(L.logoutAction) {
            try model.signOut(account.id)
            QuotaMonitor.shared.note("已退出 Antigravity 独立账号 · \(account.name)")
        }
    }

    @MainActor private func remove() {
        guard PanelController.shared.confirm(title: L.deleteAccountTitle(account.name),
                                             message: "从列表移除此独立账号。已有账号目录会移到本机的 _deleted 备份目录。",
                                             okTitle: L.deleteAccountAction, destructive: true) else { return }
        perform(L.deleteAccountAction) {
            let backup = try model.removeAccount(account.id)
            if let backup {
                QuotaMonitor.shared.note("已移除 Antigravity 账号。备份在 \(backup.path)")
            } else {
                QuotaMonitor.shared.note("已移除 Antigravity 账号 · \(account.name)")
            }
        }
    }

    @MainActor private func perform(_ title: String, _ action: () throws -> Void) {
        do { try action() }
        catch { PanelController.shared.info(title: title, message: error.localizedDescription) }
    }
}
