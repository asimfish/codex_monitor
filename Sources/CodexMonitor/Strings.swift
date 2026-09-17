import Foundation

/// All user-facing text lives here (Chinese UI). Logic files stay ASCII-only.
enum L {
    // Generic
    static let dash = "—"
    static let separator = " · "
    static let ok = "确定"
    static let cancel = "取消"
    static let gotIt = "好"
    static func paren(_ s: String) -> String { "（\(s)）" }

    // Time
    static let countdownReached = "已到"
    static let dayUnit = "天"
    static let hourUnit = "小时"
    static let minuteUnit = "分"
    static let daysLeftPast = "已过"
    static func daysLeft(_ n: Int) -> String { "剩 \(n) 天" }
    static func hoursLeft(_ n: Int) -> String { "剩 \(n) 小时" }
    static let justNow = "刚刚"
    static func minutesAgo(_ n: Int) -> String { "\(n) 分钟前" }
    static func hoursAgo(_ n: Int) -> String { "\(n) 小时前" }

    // Rate-limit windows
    static let windowDefault = "额度"
    static let window5h = "5 小时"
    static let windowDaily = "每天"
    static let windowWeekly = "每周"
    static func windowDays(_ n: Int) -> String { "\(n) 天" }
    static func windowHours(_ n: Int) -> String { "\(n) 小时" }
    static func windowMinutes(_ n: Int) -> String { "\(n) 分" }

    // Store / errors
    static func errMissingAuth(_ p: String) -> String { "找不到 auth.json：\(p)" }
    static func errInvalidName(_ n: String) -> String { "无效的账号名：\(n)" }
    static func errAlreadyExists(_ n: String) -> String { "账号目录已存在：\(n)" }
    static func errAuthParse(_ m: String) -> String { "auth.json 解析失败：\(m)" }
    static let errNotJSONObject = "auth.json 不是 JSON 对象"
    static let errNoExecutable = "找不到可执行文件路径"
    static func adopted(_ name: String) -> String { "已把 ~/.codex 中刷新过的 token 同步到账号「\(name)」" }
    static func adoptFailed(_ name: String, _ err: String) -> String { "同步 token 到「\(name)」失败：\(err)" }
    static func archived(_ name: String) -> String { "已将原账号保存为「\(name)」" }
    static func backedUp(_ path: String) -> String { "原 ~/.codex/auth.json 已备份到 \(path)" }
    static func switched(_ name: String) -> String { "已切换到「\(name)」" }

    // Network errors
    static let errNoToken = "没有 ChatGPT access token（可能是 API key 模式）"
    static func errUnauthorized(_ m: String) -> String { "Token 失效/过期（401）" + (m.isEmpty ? "" : "：\(m)") }
    static func errHTTP(_ code: Int, _ m: String) -> String { "HTTP \(code)" + (m.isEmpty ? "" : "：\(m)") }
    static func errNetwork(_ m: String) -> String { "网络错误：\(m)" }
    static func errDecode(_ m: String) -> String { "解析失败：\(m)" }

    // Monitor log
    static let authChanged = "检测到 auth.json 变化，重新加载"
    static func tokenExpiredNeedRefresh(_ exp: String) -> String { "access token 已于 \(exp) 过期，刷新后才能读取额度" }
    static func switchFailed(_ err: String) -> String { "切换失败：\(err)" }
    static func savedAsProfile(_ name: String) -> String { "已把当前登录保存为账号「\(name)」" }
    static func saveFailed(_ err: String) -> String { "保存失败：\(err)" }
    static func noRefreshToken(_ name: String) -> String { "「\(name)」没有 refresh_token，无法刷新" }
    static func tokensRefreshed(_ name: String) -> String { "已刷新「\(name)」的 token" }
    static func refreshFailed(_ name: String, _ err: String) -> String { "刷新「\(name)」失败：\(err)" }

    // Window modes
    static let modeTop = "置顶显示"
    static let modeNormal = "普通窗口"
    static let modeDesktop = "贴在桌面"

    // Widget chrome
    static let appTitle = "Codex 额度"
    static let refreshNow = "立即刷新"
    static let settings = "设置"
    static let hidePanelHelp = "隐藏悬窗（可从菜单栏图标重新显示）"
    static let refreshInterval = "刷新间隔"
    static func minutes(_ n: Int) -> String { "\(n) 分钟" }
    static let windowLevel = "窗口层级"
    static let launchAtLogin = "开机自启"
    static let launchAtLoginFailed = "设置开机自启失败"
    static let addAccount = "添加账号"
    static let addAccountEllipsis = "添加账号…"
    static let openAccountsFolder = "打开账号目录"
    static let openUsagePage = "打开 ChatGPT 用量页"
    static let showLog = "显示日志"
    static let hideLog = "隐藏日志"
    static let quit = "退出 Codex Monitor"
    static let hidePanel = "隐藏悬窗"
    static let showPanel = "显示悬窗"
    static let noEvents = "（暂无事件）"
    static let noLoginsFound = "没有找到 Codex 登录"
    static let emptyTitle = "还没有找到任何 Codex 登录"
    static let emptyBody = "在终端运行 codex login，或点击下方「添加账号」用设备码登录一个新账号。"
    static func accountsCount(_ n: Int, _ path: String) -> String { "\(n) 个账号 · \(path)" }

    // Add account
    static let addAccountTitle = "添加 Codex 账号"
    static let addAccountMessage = "给这个账号起个目录名（例如 work、alt1）。接下来会在终端运行 codex-acct add <名字>：它用一个隔离的 CODEX_HOME 执行设备码登录，不会碰现有的 ~/.codex/auth.json。请在浏览器的隐身窗口里用另一个 ChatGPT 账号完成登录。"

    // Account card
    static func cachedFrom(_ time: String) -> String { "显示的是 \(time) 的缓存数据" }
    static let inUse = "使用中"
    static let saveAsProfile = "存为账号"
    static let saveAsProfileHelp = "把 ~/.codex/auth.json 存进 ~/.codex-accounts，方便以后切回来"
    static let switchBtn = "切换"
    static let switchHelp = "把这个账号写入 ~/.codex/auth.json"
    static func limitReached(_ type: String?) -> String { "已达到额度上限" + (type.map { "（\($0)）" } ?? "") }
    static let loadingUsage = "正在读取额度…"
    static func creditsBalance(_ s: String) -> String { "额度包余额：\(s)" }
    static let unlimited = "无限"
    static let otherLimit = "其他"
    static func remaining(_ pct: String) -> String { "剩余 \(pct)" }
    static func remainingShort(_ pct: String) -> String { "剩 \(pct)" }
    static func resetAt(_ time: String, _ countdown: String) -> String { "重置 \(time) · \(countdown)后" }
    static func resetShort(_ time: String) -> String { "重置 \(time)" }
    static let resetCredits = "重置次数"
    static let subscriptionExpiry = "订阅周期（登录时快照）"
    static let tokenValidity = "登录凭证（自动续期）"
    static let lastRefresh = "凭证最后刷新"
    static func times(_ n: Int) -> String { "\(n) 次" }
    static func timesWithExpiry(_ n: Int, _ date: String) -> String { "\(n) 次 · 最早 \(date) 到期" }
    static let expiredPendingRefresh = "（已过·待刷新）"
    static let expired = "（已过期）"
    static let readFailed = "读取失败"
    static func resetCoupons(_ n: Int) -> String { "重置券 \(n)" }

    // Token refresh
    static let refreshTokenBtn = "刷新 Token…"
    static let refreshTokenHelp = "用 refresh_token 换新 token 并改写 auth.json（需确认）"
    static func refreshConfirmTitle(_ name: String) -> String { "刷新「\(name)」的 Token？" }
    static let refreshConfirmMessage = "会向 auth.openai.com 用 refresh_token 换取新 token，并改写该账号的 auth.json（如果它正在使用中，也会同步改写 ~/.codex/auth.json）。旧的 refresh_token 可能随即失效：如果你把这份 auth.json 拷到了别的机器，那边之后可能需要重新拷贝。"
    static let refreshAction = "刷新"

    // Switch
    static func switchConfirmTitle(_ name: String) -> String { "切换到 \(name)？" }
    static let switchConfirmMessage = "会把 ~/.codex/auth.json 替换为该账号的凭证。当前凭证会先同步回它所属的账号目录（或备份到 ~/.codex-accounts/_backup）。已经打开的 Codex App / CLI 会话需要重启才会使用新账号。"
    static let menuSwitchMessage = "会替换 ~/.codex/auth.json；已打开的 Codex 会话需重启。"
    static let switchAction = "切换"

    // Save as profile
    static let saveProfileTitle = "保存当前登录为账号"
    static let saveProfileMessage = "会把 ~/.codex/auth.json 复制到 ~/.codex-accounts/<名称>/auth.json。"
}
