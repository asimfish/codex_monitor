import Foundation

/// Text for the browser (OAuth) login path, the device-code fallback and polling.
extension L {
    // Data source caption under the quota bars
    static func sourceLive(_ t: String) -> String { "实时 · 来自 Codex 会话事件 \(t)" }
    static func sourceAPI(_ t: String) -> String { "接口 · 上次拉取 \(t)" }

    // Collapsible layout
    static let collapsePanel = "折叠为小条（只显示当前账号）"
    static let expandPanel = "展开面板"
    static let collapseCard = "收起这个账号"
    static let expandCard = "点击展开这个账号的详情"
    static let dragHint = "按住拖动；点底部的箭头才会展开"
    static func resetIn(_ countdown: String) -> String { "\(countdown)后重置" }

    static func otherAccounts(_ n: Int) -> String { "其他账号 · \(n)" }
    static let expandAll = "全部展开"
    static let collapseAll = "全部收起"
    static let automaticAccountOrder = "自动排序"
    static let automaticAccountOrderHelp = "优先显示有额度且凭证未过期的账号"
    static let reorderAccounts = "调整顺序"
    static let finishOrdering = "完成"
    static let moveAccountUp = "上移"
    static let moveAccountDown = "下移"
    static let moveAccountFirst = "移到顶部"
    static let moveAccountLast = "移到底部"
    static func accountPosition(_ n: Int) -> String { "第 \(n) 位" }

    // Per-account actions
    static let accountActionsHelp = "账号操作：复制、导出、重新登录或退出登录"
    static let copyAuthJSON = "复制 auth.json 内容"
    static let copyAuthPath = "复制 auth.json 路径"
    static let revealInFinder = "在 Finder 中显示"
    static let exportAuth = "导出 auth.json…"
    static func copiedAuth(_ name: String) -> String { "已把「\(name)」的 auth.json 内容复制到剪贴板（含 token，注意保密）" }
    static func copiedPath(_ path: String) -> String { "已复制路径 \(path)" }
    static func exportedAuth(_ name: String, _ path: String) -> String { "已把「\(name)」的 auth.json 导出到 \(path)" }
    static func copyFailed(_ err: String) -> String { "复制/导出失败：\(err)" }

    static func savedLoginExists(_ name: String) -> String { "账号「\(name)」已有登录凭证，请从账号菜单选择“重新登录此账号”。" }

    static let logoutAction = "退出登录"
    static func logoutTitle(_ name: String) -> String { "退出「\(name)」？" }
    static let logoutMessage = "将移除该账号在本机的登录凭证（包括重复存档）；如果它是当前使用账号，也会清除 Codex 当前登录。账号会保留在列表中，之后可以直接重新登录。配置、会话记录和历史备份也会保留，不会退出其他设备或浏览器。正在运行的 Codex 进程可能仍持有凭证，请自行关闭。"
    static let logoutAccountChanged = "账号凭证已变化，请刷新列表后重试。"
    static let logoutLoginRunning = "请先完成或取消正在进行的登录，再退出账号。"
    static func loggedOut(_ name: String) -> String { "已在本机退出「\(name)」" }

    // Re-login
    static let reloginEllipsis = "重新登录此账号…"
    static let reloginHelp = "会话被服务端作废时，用同一个目录重新走一遍登录，覆盖旧凭证"
    static func reloginConfirmTitle(_ name: String) -> String { "重新登录「\(name)」？" }
    static func reloginConfirmMessage(_ path: String) -> String { "登录成功后会覆盖 \(path)。请在浏览器里用同一个 ChatGPT 账号登录。" }
    static let reloginAction = "重新登录"

    // Automatic refresh on 401
    static let autoRefreshToggle = "Token 被拒绝(401)时自动刷新一次"
    static func autoRefreshed(_ name: String) -> String { "「\(name)」的 token 被服务端拒绝，已用 refresh_token 自动换新并重新拉取" }
    static func autoRefreshFailed(_ name: String, _ err: String) -> String { "「\(name)」自动刷新失败：\(err)" }
    static let sessionRevoked = "会话已被服务端作废（refresh_token 也失效），请「重新登录此账号」"

    static let subscriptionRenewedPending = "接口显示付费中 · 新周期需重新登录后显示"
    static let subscriptionEnded = "（已到期）"

    static func checkedAtLogin(_ time: String) -> String { "登录时核验 \(time)" }
    static let subscriptionSnapshotHelp = "订阅周期来自登录那一刻签发的 id_token；token 刷新不会重新核验，接口也不提供实时周期。重新登录此账号可刷新这个快照。"
    static func credentialUntil(_ time: String) -> String { "至 \(time) · 10 天滚动" }

    // Polling
    static func intervalLabel(seconds s: Int) -> String {
        if s < 60 { return "\(s) 秒" }
        return "\(s / 60) 分钟"
    }
    static let rateLimitedBackoff = "接口返回 429（请求过于频繁），自动刷新暂停 2 分钟；手动刷新不受影响"

    // Login modes
    static let loginSwitchToDevice = "改用设备码登录（远程 / 无浏览器时）"
    static let loginSwitchToBrowser = "改用浏览器登录（推荐）"
    static let loginRetryBrowser = "重试：浏览器登录"
    static let loginRetryDevice = "重试：设备码登录"

    // Browser flow
    static let loginBrowserStep1 = "在浏览器里用要添加的 ChatGPT 账号登录"
    static let loginBrowserHint = "推荐用隐身/无痕窗口打开：普通窗口会直接用当前已登录的 ChatGPT 账号。登录完成后页面会跳回本机 localhost:1455，这里会自动变成「登录成功」。这一步不需要在 ChatGPT 里开启任何设置。"
    static let loginBrowserWaiting = "等待浏览器完成登录…"
    static let loginExchanging = "已收到授权回调，正在换取 token…"

    // Device-code flow
    static let loginDeviceNeedsSetting = "设备码授权在 ChatGPT 里默认是关闭的：先用要添加的账号登录 ChatGPT，在「设置 → 安全」里打开 Codex 的「设备代码授权」，再来输入代码；否则会提示「请在 ChatGPT 安全设置中为 Codex 启用设备代码授权」。"
    static let loginOpenSecuritySettings = "打开 ChatGPT 安全设置（隐身窗口）"

    // OAuth errors
    static let oauthPortBusy = "本机 1455 端口被占用（可能有另一个 codex login 或 Codex 登录流程正在进行），关掉后重试。"
    static func oauthListenerFailed(_ m: String) -> String { "无法监听 localhost:1455：\(m)" }
    static let oauthStateMismatch = "回调里的 state 不匹配，已忽略（可能来自别的登录流程）。"
    static func oauthProviderError(_ code: String, _ desc: String?) -> String { "授权被拒绝：\(code)" + (desc.map { "（\($0)）" } ?? "") }
    static func oauthTokenExchangeFailed(_ status: Int, _ body: String) -> String { "换取 token 失败（HTTP \(status)）" + (body.isEmpty ? "" : "：\(body)") }
    static let oauthMissingTokens = "授权服务器没有返回完整的 token。"

    // Pages shown in the browser after the redirect
    static let oauthSuccessHTML = """
    <!doctype html><html lang="zh"><head><meta charset="utf-8"><title>Codex 登录成功</title>
    <style>body{font-family:-apple-system,system-ui,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:#f5f5f7;color:#1d1d1f}
    .card{background:#fff;border-radius:16px;padding:40px 48px;box-shadow:0 8px 30px rgba(0,0,0,.08);text-align:center}h1{font-size:22px;margin:0 0 8px}p{margin:0;color:#6e6e73}</style></head>
    <body><div class="card"><h1>登录成功</h1><p>凭证已保存到 Codex Monitor，可以关闭这个窗口了。</p></div></body></html>
    """
    static func oauthFailureHTML(_ err: String) -> String {
        """
        <!doctype html><html lang="zh"><head><meta charset="utf-8"><title>Codex 登录失败</title>
        <style>body{font-family:-apple-system,system-ui,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:#f5f5f7;color:#1d1d1f}
        .card{background:#fff;border-radius:16px;padding:40px 48px;box-shadow:0 8px 30px rgba(0,0,0,.08);text-align:center}h1{font-size:22px;margin:0 0 8px}p{margin:0;color:#6e6e73}</style></head>
        <body><div class="card"><h1>登录没有完成</h1><p>授权服务器返回：\(err)。回到 Codex Monitor 可以重试。</p></div></body></html>
        """
    }
}
