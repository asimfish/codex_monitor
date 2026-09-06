import Foundation

/// Text for the in-app device-code login window.
extension L {
    static let loginCodexNotFound = "找不到 codex 命令。请先安装 Codex CLI（npm i -g @openai/codex），或在终端用 codex-acct add <名字> 登录。"
    static func loginExited(_ code: Int) -> String { "codex login 退出（状态码 \(code)），没有生成 auth.json。" }
    static let loginPromptMessage = "给这个账号起个目录名（例如 work、alt1）。接下来会在一个独立的 CODEX_HOME 里做设备码登录，不会碰现有的 ~/.codex/auth.json。"
    static func loginWindowTitle(_ name: String) -> String { "登录新账号「\(name)」" }
    static let loginPreparing = "正在启动 codex login，获取设备码…"
    static let loginCancelled = "已取消。"
    static let loginDone = "完成"
    static let loginStep1 = "1. 在浏览器打开登录页"
    static let loginStep1Hint = "建议用隐身/无痕窗口：否则浏览器会直接用当前已登录的 ChatGPT 账号授权，就加不了新号了。点击按钮时代码会自动复制到剪贴板。"
    static func loginOpenPrivate(_ app: String) -> String { "用 \(app) 隐身窗口打开" }
    static let loginOpenDefault = "用默认浏览器打开"
    static let loginStep2 = "2. 在页面里输入这个一次性代码（15 分钟内有效）"
    static let loginCopy = "复制代码"
    static let loginCopied = "已复制 ✓"
    static let loginWaiting = "等待你在浏览器完成登录…（登录成功后这里会自动更新）"
    static let loginSuccessTitle = "登录成功"
    static func loginSuccessHint(_ name: String) -> String { "凭证已保存到 ~/.codex-accounts/\(name)/auth.json，悬窗里已经能看到它的额度。现在 ~/.codex 仍然是原来的账号；点下面按钮可以直接切过去。" }
    static let loginMakeActive = "设为当前账号（写入 ~/.codex/auth.json）"
    static let loginFailedTitle = "登录没有完成"
    static func loginFallbackHint(_ name: String) -> String { "也可以在终端手动执行：codex-acct add \(name)" }
}
