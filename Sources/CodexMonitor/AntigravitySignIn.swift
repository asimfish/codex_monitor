import AppKit
import SwiftUI

enum AntigravitySignInResult {
    case authorized
    case verification(AntigravityCLI.Eligibility)
    case cancelled
    case failed(String)

    var canRetry: Bool {
        switch self {
        case .authorized, .verification: return false
        case .cancelled, .failed: return true
        }
    }
}

@MainActor
final class AntigravitySignInWindow {
    static let shared = AntigravitySignInWindow()
    private var window: NSWindow?
    private var accountID: UUID?

    func show(account: AntigravityAccount, model: AntigravityModel) {
        if window == nil || accountID != account.id {
            window?.close()
            let window = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 460, height: 285),
                styleMask: [.titled, .closable],
                backing: .buffered,
                defer: false
            )
            window.isReleasedWhenClosed = false
            window.title = "Antigravity 登录"
            window.collectionBehavior = [.moveToActiveSpace]
            window.contentView = NSHostingView(rootView: AntigravitySignInView(account: account, model: model) {
                Self.shared.window?.close()
            }.frame(width: 460).frame(minHeight: 285)
                .background(Color(nsColor: .windowBackgroundColor)))
            window.center()
            self.window = window
            accountID = account.id
        }
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
}

private struct AntigravitySignInView: View {
    let account: AntigravityAccount
    @ObservedObject var model: AntigravityModel
    var close: () -> Void
    @State private var code = ""
    @FocusState private var codeFocused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("登录 Antigravity").font(.title2.bold())
            Text(account.name).foregroundStyle(.secondary).lineLimit(1)

            if let result = model.loginResult {
                switch result {
                case .authorized:
                    Label("登录已保存，面板会继续同步额度。", systemImage: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                    if let email = model.saved[account.id]?.email {
                        Text("Google 账号：\(email)").font(.callout).textSelection(.enabled)
                    }
                case .verification(let eligibility):
                    AntigravityVerificationNotice(eligibility: eligibility)
                case .cancelled:
                    Text("登录已取消，可以重新开始。")
                case .failed(let reason):
                    Text(reason).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
                HStack {
                    if result.canRetry {
                        Button("重新登录") { Task { await model.login(account) } }
                            .disabled(!model.signingIn.isEmpty)
                    }
                    Spacer()
                    Button("关闭", action: close).keyboardShortcut(.defaultAction)
                }
            } else {
                Text("在 Google 网页选择要登录的账号，再复制授权码。在下方粘贴，或直接点「完成登录」提交刚复制的授权码。")
                    .fixedSize(horizontal: false, vertical: true)
                SecureField("粘贴 Google 授权码", text: $code)
                    .textFieldStyle(.roundedBorder)
                    .accessibilityIdentifier("antigravity-authorization-code")
                    .focused($codeFocused)
                    .disabled(model.loginURL == nil || model.submittingCode)
                    .onChange(of: model.loginURL) { _, url in if url != nil { codeFocused = true } }
                if let hint = model.loginHint {
                    Text(hint).foregroundStyle(.red).font(.caption)
                } else if model.loginURL == nil || model.submittingCode {
                    HStack(spacing: 6) {
                        ProgressView().controlSize(.small)
                        Text(model.submittingCode ? "正在核验授权…" : "正在准备 Google 登录…")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
                Spacer()
                HStack(spacing: 8) {
                    Button("重新打开隐身窗口") { model.openLoginBrowser() }
                        .buttonStyle(.link).disabled(model.loginURL == nil || model.submittingCode)
                    Button("用普通浏览器打开") { model.openLoginBrowser(regular: true) }
                        .buttonStyle(.link).disabled(model.loginURL == nil || model.submittingCode)
                    Spacer()
                    Button("取消") { code = ""; model.cancelLogin() }.keyboardShortcut(.cancelAction)
                    Button("完成登录") {
                        if code.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                            model.submitCopiedCode()
                        } else if model.submitCode(code) {
                            code = ""
                        }
                    }.keyboardShortcut(.defaultAction)
                        .disabled(model.loginURL == nil || model.submittingCode)
                }
            }
        }
        .padding(20)
        .onChange(of: model.loginResult != nil) { _, finished in if finished { code = "" } }
        .onDisappear { code = "" }
    }
}

struct AntigravityVerificationNotice: View {
    let eligibility: AntigravityCLI.Eligibility

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(AntigravityCLI.Eligibility.message).foregroundStyle(.secondary)
            if let url = eligibility.url {
                Button("去 Google 验证") { NSWorkspace.shared.open(url) }.buttonStyle(.link)
                Text("用此账号完成验证后，回到悬浮窗刷新。").foregroundStyle(.secondary)
            }
        }.font(.caption)
    }
}
