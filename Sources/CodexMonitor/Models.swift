import Foundation

// MARK: - auth.json

struct AuthTokens: Codable {
    var idToken: String?
    var accessToken: String?
    var refreshToken: String?
    var accountId: String?

    enum CodingKeys: String, CodingKey {
        case idToken = "id_token"
        case accessToken = "access_token"
        case refreshToken = "refresh_token"
        case accountId = "account_id"
    }
}

struct AuthFile: Codable {
    var authMode: String?
    var openaiApiKey: String?
    var tokens: AuthTokens?
    var lastRefresh: String?

    enum CodingKeys: String, CodingKey {
        case authMode = "auth_mode"
        case openaiApiKey = "OPENAI_API_KEY"
        case tokens
        case lastRefresh = "last_refresh"
    }

    var lastRefreshDate: Date? { lastRefresh.flatMap(ISO8601.parse) }
    var isChatGPTAuth: Bool { tokens?.accessToken != nil }
    var hasLoginCredentials: Bool {
        [tokens?.accessToken, openaiApiKey].contains { value in
            guard let value = value else { return false }
            return !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }
    }

    static func load(from url: URL) throws -> AuthFile {
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(AuthFile.self, from: data)
    }
}

// MARK: - JWT

enum JWT {
    static func claims(_ token: String) -> [String: Any]? {
        let parts = token.split(separator: ".")
        guard parts.count >= 2 else { return nil }
        var payload = String(parts[1])
            .replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")
        while payload.count % 4 != 0 { payload.append("=") }
        guard let data = Data(base64Encoded: payload),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        return obj
    }

    static func epochDate(_ value: Any?) -> Date? {
        if let n = value as? NSNumber { return Date(timeIntervalSince1970: n.doubleValue) }
        return nil
    }
}

// MARK: - ISO 8601

enum ISO8601 {
    private static let withFraction: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    private static let plain: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    /// Accepts "2026-09-05T07:52:32.639755Z", "2026-09-06T12:52:19+00:00", etc.
    /// Foundation only parses exactly 3 fractional digits, so normalise first.
    static func parse(_ raw: String) -> Date? {
        let s = raw.trimmingCharacters(in: .whitespaces)
        guard let tIdx = s.firstIndex(of: "T") else { return plain.date(from: s) }
        if let dot = s[tIdx...].firstIndex(of: ".") {
            var end = s.index(after: dot)
            while end < s.endIndex, s[end].isNumber { end = s.index(after: end) }
            var frac = String(s[s.index(after: dot)..<end])
            if frac.count > 3 { frac = String(frac.prefix(3)) }
            while frac.count < 3 { frac += "0" }
            let normalised = String(s[..<dot]) + "." + frac + String(s[end...])
            return withFraction.date(from: normalised)
        }
        return plain.date(from: s)
    }

    static func format(_ date: Date) -> String { withFraction.string(from: date) }
}

// MARK: - Identity derived from JWT claims

struct Identity {
    var email: String?
    var name: String?
    var planType: String?
    var accountId: String?
    var userId: String?
    var subscriptionStart: Date?
    var subscriptionUntil: Date?
    var subscriptionLastChecked: Date?
    var accessTokenIssued: Date?
    var accessTokenExpiry: Date?
    var idTokenExpiry: Date?

    init(auth: AuthFile) {
        let authKey = "https://api.openai.com/auth"
        if let idToken = auth.tokens?.idToken, let c = JWT.claims(idToken) {
            email = c["email"] as? String
            name = c["name"] as? String
            idTokenExpiry = JWT.epochDate(c["exp"])
            if let a = c[authKey] as? [String: Any] {
                planType = a["chatgpt_plan_type"] as? String
                accountId = a["chatgpt_account_id"] as? String
                userId = a["chatgpt_user_id"] as? String
                subscriptionStart = (a["chatgpt_subscription_active_start"] as? String).flatMap(ISO8601.parse)
                subscriptionUntil = (a["chatgpt_subscription_active_until"] as? String).flatMap(ISO8601.parse)
                subscriptionLastChecked = (a["chatgpt_subscription_last_checked"] as? String).flatMap(ISO8601.parse)
            }
        }
        if let accessToken = auth.tokens?.accessToken, let c = JWT.claims(accessToken) {
            accessTokenIssued = JWT.epochDate(c["iat"])
            accessTokenExpiry = JWT.epochDate(c["exp"])
            if let a = c[authKey] as? [String: Any] {
                if planType == nil { planType = a["chatgpt_plan_type"] as? String }
                if accountId == nil { accountId = a["chatgpt_account_id"] as? String }
                if userId == nil { userId = a["chatgpt_user_id"] as? String }
            }
            if let p = c["https://api.openai.com/profile"] as? [String: Any] {
                if email == nil { email = p["email"] as? String }
                if name == nil { name = p["name"] as? String }
            }
        }
        if accountId == nil { accountId = auth.tokens?.accountId }
    }

    var accessTokenExpired: Bool {
        guard let exp = accessTokenExpiry else { return false }
        return exp < Date()
    }
}

// MARK: - Usage API (GET {chatgpt_base_url}/wham/usage)

struct RateLimitWindow: Codable {
    var usedPercent: Double
    var limitWindowSeconds: Int?
    var resetAfterSeconds: Int?
    var resetAt: Double?

    enum CodingKeys: String, CodingKey {
        case usedPercent = "used_percent"
        case limitWindowSeconds = "limit_window_seconds"
        case resetAfterSeconds = "reset_after_seconds"
        case resetAt = "reset_at"
    }

    var remainingPercent: Double { max(0, min(100, 100 - usedPercent)) }
    var resetDate: Date? { resetAt.map { Date(timeIntervalSince1970: $0) } }
    var label: String { Fmt.windowLabel(seconds: limitWindowSeconds) }
}

struct RateLimit: Codable {
    var allowed: Bool?
    var limitReached: Bool?
    var primaryWindow: RateLimitWindow?
    var secondaryWindow: RateLimitWindow?

    enum CodingKeys: String, CodingKey {
        case allowed
        case limitReached = "limit_reached"
        case primaryWindow = "primary_window"
        case secondaryWindow = "secondary_window"
    }

    /// Windows sorted short to long (5h before weekly).
    var windows: [RateLimitWindow] {
        [primaryWindow, secondaryWindow].compactMap { $0 }
            .sorted { ($0.limitWindowSeconds ?? 0) < ($1.limitWindowSeconds ?? 0) }
    }
}

struct AdditionalRateLimit: Codable {
    var limitName: String?
    var meteredFeature: String?
    var rateLimit: RateLimit?

    enum CodingKeys: String, CodingKey {
        case limitName = "limit_name"
        case meteredFeature = "metered_feature"
        case rateLimit = "rate_limit"
    }
}

struct CreditsInfo: Codable {
    var hasCredits: Bool?
    var unlimited: Bool?
    var overageLimitReached: Bool?
    var balance: String?

    enum CodingKeys: String, CodingKey {
        case hasCredits = "has_credits"
        case unlimited
        case overageLimitReached = "overage_limit_reached"
        case balance
    }
}

struct ResetCreditsSummary: Codable {
    var availableCount: Int?
    var applicableAvailableCount: Int?

    enum CodingKeys: String, CodingKey {
        case availableCount = "available_count"
        case applicableAvailableCount = "applicable_available_count"
    }
}

/// `rate_limit_reached_type` is a plain string in some responses and an object such as
/// `{"type":"rate_limit_reached","details":"default"}` once the limit is actually hit.
struct RateLimitReachedType: Codable {
    var type: String?
    var details: String?

    init(type: String?, details: String? = nil) {
        self.type = type
        self.details = details
    }

    init(from decoder: Decoder) throws {
        let single = try decoder.singleValueContainer()
        if let s = try? single.decode(String.self) {
            type = s
            details = nil
            return
        }
        struct Object: Decodable {
            var type: String?
            var details: String?
        }
        if let o = try? single.decode(Object.self) {
            type = o.type
            details = o.details
            return
        }
        type = nil
        details = nil
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encodeIfPresent(type, forKey: .type)
        try c.encodeIfPresent(details, forKey: .details)
    }

    private enum CodingKeys: String, CodingKey {
        case type
        case details
    }

    /// Extra detail worth showing next to "limit reached"; nil for the generic case.
    var displayDetail: String? {
        guard let t = type, t != "rate_limit_reached" else {
            return (details == nil || details == "default") ? nil : details
        }
        return t
    }
}

struct UsageResponse: Codable {
    var userId: String?
    var accountId: String?
    var email: String?
    var planType: String?
    var rateLimit: RateLimit?
    var additionalRateLimits: [AdditionalRateLimit]?
    var credits: CreditsInfo?
    var rateLimitReachedType: RateLimitReachedType?
    var rateLimitResetCredits: ResetCreditsSummary?

    enum CodingKeys: String, CodingKey {
        case userId = "user_id"
        case accountId = "account_id"
        case email
        case planType = "plan_type"
        case rateLimit = "rate_limit"
        case additionalRateLimits = "additional_rate_limits"
        case credits
        case rateLimitReachedType = "rate_limit_reached_type"
        case rateLimitResetCredits = "rate_limit_reset_credits"
    }
}

// MARK: - Reset credits (GET {chatgpt_base_url}/wham/rate-limit-reset-credits)

struct ResetCredit: Codable {
    var id: String?
    var resetType: String?
    var status: String?
    var isSupportedByPlan: Bool?
    var grantedAt: String?
    var expiresAt: String?
    var title: String?

    enum CodingKeys: String, CodingKey {
        case id
        case resetType = "reset_type"
        case status
        case isSupportedByPlan = "is_supported_by_plan"
        case grantedAt = "granted_at"
        case expiresAt = "expires_at"
        case title
    }

    var expiresDate: Date? { expiresAt.flatMap(ISO8601.parse) }
}

struct ResetCreditsResponse: Codable {
    var credits: [ResetCredit]?
    var availableCount: Int?
    var totalEarnedCount: Int?

    enum CodingKeys: String, CodingKey {
        case credits
        case availableCount = "available_count"
        case totalEarnedCount = "total_earned_count"
    }

    var earliestExpiry: Date? {
        (credits ?? [])
            .filter { ($0.status ?? "available") == "available" }
            .compactMap { $0.expiresDate }
            .min()
    }
}

// MARK: - Cached snapshot

struct UsageSnapshot: Codable {
    var fetchedAt: Date
    var usage: UsageResponse
    var resetCredits: ResetCreditsResponse?
}

// MARK: - Token refresh (POST https://auth.openai.com/oauth/token)

struct TokenRefreshResponse: Codable {
    var idToken: String?
    var accessToken: String?
    var refreshToken: String?

    enum CodingKeys: String, CodingKey {
        case idToken = "id_token"
        case accessToken = "access_token"
        case refreshToken = "refresh_token"
    }
}

// MARK: - Terminal output scanning (codex login --device-auth)

enum TextScan {
    // ICU regex syntax: use \x{1B} for ESC. (Swift's \u{1B} is not valid inside a raw-string pattern.)
    private static let ansi = try! NSRegularExpression(pattern: #"\x{1B}\[[0-9;?]*[ -/]*[@-~]"#)
    private static let url = try! NSRegularExpression(pattern: #"https://[^\s\x{1B}]+"#)
    private static let code = try! NSRegularExpression(pattern: #"(?<![A-Z0-9])[A-Z0-9]{4,8}-[A-Z0-9]{4,8}(?![A-Z0-9])"#)

    static func stripANSI(_ s: String) -> String {
        let range = NSRange(s.startIndex..., in: s)
        return ansi.stringByReplacingMatches(in: s, range: range, withTemplate: "")
    }

    static func first(_ re: NSRegularExpression, in text: String) -> String? {
        let range = NSRange(text.startIndex..., in: text)
        guard let m = re.firstMatch(in: text, range: range), let r = Range(m.range, in: text) else { return nil }
        return String(text[r])
    }

    /// The verification URL and one-time user code printed by `codex login --device-auth`.
    static func deviceLogin(in transcript: String) -> (url: String, code: String)? {
        let clean = stripANSI(transcript)
        guard let u = first(url, in: clean), let c = first(code, in: clean) else { return nil }
        return (u, c)
    }
}

// MARK: - Formatting helpers

enum Fmt {
    static let dateTime: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale.current
        f.dateFormat = "MM-dd HH:mm"
        return f
    }()

    static let fullDateTime: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale.current
        f.dateFormat = "yyyy-MM-dd HH:mm"
        return f
    }()

    static let dateOnly: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale.current
        f.dateFormat = "MM-dd"
        return f
    }()

    static let time: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale.current
        f.dateFormat = "HH:mm:ss"
        return f
    }()

    /// e.g. "6d 18h" / "3h 12m" / "5m" (localised units come from L).
    static func countdown(to date: Date, from now: Date = Date()) -> String {
        let s = Int(date.timeIntervalSince(now))
        if s <= 0 { return L.countdownReached }
        let d = s / 86400, h = (s % 86400) / 3600, m = (s % 3600) / 60
        if d > 0 { return "\(d)\(L.dayUnit)\(h)\(L.hourUnit)" }
        if h > 0 { return "\(h)\(L.hourUnit)\(m)\(L.minuteUnit)" }
        return "\(max(m, 1))\(L.minuteUnit)"
    }

    /// Single-unit countdown for tight spaces: "29d" style using localised units.
    static func shortCountdown(to date: Date, from now: Date = Date()) -> String {
        let s = Int(date.timeIntervalSince(now))
        if s <= 0 { return L.countdownReached }
        if s >= 86400 { return "\(s / 86400)\(L.dayUnit)" }
        if s >= 3600 { return "\(s / 3600)\(L.hourUnit)" }
        return "\(max(1, s / 60))\(L.minuteUnit)"
    }

    static func daysLeft(until date: Date, from now: Date = Date()) -> String {
        let s = date.timeIntervalSince(now)
        if s <= 0 { return L.daysLeftPast }
        let days = Int(s / 86400)
        if days >= 1 { return L.daysLeft(days) }
        return L.hoursLeft(max(1, Int(s / 3600)))
    }

    static func ago(_ date: Date, now: Date = Date()) -> String {
        let s = Int(now.timeIntervalSince(date))
        if s < 60 { return L.justNow }
        if s < 3600 { return L.minutesAgo(s / 60) }
        if s < 86400 { return L.hoursAgo(s / 3600) }
        return dateTime.string(from: date)
    }

    static func windowLabel(seconds: Int?) -> String {
        guard let s = seconds, s > 0 else { return L.windowDefault }
        switch s {
        case 18000: return L.window5h
        case 86400: return L.windowDaily
        case 604800: return L.windowWeekly
        default:
            if s % 86400 == 0 { return L.windowDays(s / 86400) }
            if s % 3600 == 0 { return L.windowHours(s / 3600) }
            return L.windowMinutes(s / 60)
        }
    }

    static func planLabel(_ plan: String?) -> String {
        guard let p = plan, !p.isEmpty else { return L.dash }
        switch p.lowercased() {
        case "pro": return "Pro"
        case "plus": return "Plus"
        case "free": return "Free"
        case "team": return "Team"
        case "business": return "Business"
        case "enterprise": return "Enterprise"
        case "edu": return "Edu"
        case "go": return "Go"
        default: return p.capitalized
        }
    }

    static func percent(_ v: Double) -> String {
        if v.rounded() == v { return "\(Int(v))%" }
        return String(format: "%.1f%%", v)
    }
}
