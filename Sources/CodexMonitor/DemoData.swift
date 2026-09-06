import Foundation

/// `CODEX_MONITOR_DEMO=1` fills the panel with fabricated accounts and never touches the
/// network or `~/.codex`. Used for screenshots and UI work.
enum DemoData {
    static var enabled: Bool { ProcessInfo.processInfo.environment["CODEX_MONITOR_DEMO"] == "1" }

    private static func b64url(_ obj: [String: Any]) -> String {
        let data = try! JSONSerialization.data(withJSONObject: obj)
        return data.base64URLEncodedString()
    }

    private static func jwt(_ claims: [String: Any]) -> String {
        "\(b64url(["alg": "RS256", "typ": "JWT"])).\(b64url(claims)).demo"
    }

    private static func auth(email: String, plan: String, accountId: String, subscriptionUntil: Date?,
                             tokenExpiry: Date, lastRefresh: Date) -> AuthFile {
        var authClaims: [String: Any] = [
            "chatgpt_account_id": accountId,
            "chatgpt_plan_type": plan,
            "chatgpt_user_id": "user-demo-\(accountId.prefix(6))",
        ]
        if let until = subscriptionUntil {
            authClaims["chatgpt_subscription_active_until"] = ISO8601.format(until)
        }
        let idToken = jwt(["email": email, "exp": Int(tokenExpiry.timeIntervalSince1970),
                           "https://api.openai.com/auth": authClaims])
        let accessToken = jwt(["exp": Int(tokenExpiry.timeIntervalSince1970),
                               "iat": Int(lastRefresh.timeIntervalSince1970),
                               "https://api.openai.com/auth": authClaims,
                               "https://api.openai.com/profile": ["email": email]])
        return AuthFile(authMode: "chatgpt", openaiApiKey: nil,
                        tokens: AuthTokens(idToken: idToken, accessToken: accessToken,
                                           refreshToken: "rt.demo", accountId: accountId),
                        lastRefresh: ISO8601.format(lastRefresh))
    }

    private static func window(used: Double, seconds: Int, resetIn: TimeInterval, now: Date) -> RateLimitWindow {
        RateLimitWindow(usedPercent: used, limitWindowSeconds: seconds,
                        resetAfterSeconds: Int(resetIn), resetAt: now.addingTimeInterval(resetIn).timeIntervalSince1970)
    }

    private static func profile(name: String, email: String, plan: String, accountId: String,
                                subscriptionUntil: Date?, tokenExpiry: Date, lastRefresh: Date) -> Profile {
        let dir = URL(fileURLWithPath: "/demo/.codex-accounts/\(name)")
        let a = auth(email: email, plan: plan, accountId: accountId, subscriptionUntil: subscriptionUntil,
                     tokenExpiry: tokenExpiry, lastRefresh: lastRefresh)
        var p = Profile(id: name, name: name, directory: dir, authURL: dir.appendingPathComponent("auth.json"), isMain: false)
        p.auth = a
        p.identity = Identity(auth: a)
        p.modified = lastRefresh
        return p
    }

    static func entries(now: Date = Date()) -> [QuotaMonitor.Entry] {
        let day: TimeInterval = 86400
        let hour: TimeInterval = 3600

        // 1. Active Pro account: healthy, with a live rollout event 40 s ago.
        let alice = profile(name: "alice", email: "alice@example.com", plan: "pro", accountId: "acct-alice-0001",
                            subscriptionUntil: now.addingTimeInterval(21 * day), tokenExpiry: now.addingTimeInterval(8 * day),
                            lastRefresh: now.addingTimeInterval(-2 * day))
        let aliceUsage = UsageResponse(
            userId: nil, accountId: "acct-alice-0001", email: "alice@example.com", planType: "pro",
            rateLimit: RateLimit(allowed: true, limitReached: false,
                                 primaryWindow: window(used: 38, seconds: 604800, resetIn: 3 * day + 5 * hour, now: now),
                                 secondaryWindow: nil),
            additionalRateLimits: [AdditionalRateLimit(
                limitName: "GPT-5.3-Codex-Spark", meteredFeature: "codex_spark",
                rateLimit: RateLimit(allowed: true, limitReached: false,
                                     primaryWindow: window(used: 12, seconds: 18000, resetIn: 2 * hour, now: now),
                                     secondaryWindow: window(used: 4, seconds: 604800, resetIn: 5 * day, now: now)))],
            credits: CreditsInfo(hasCredits: false, unlimited: false, overageLimitReached: false, balance: "0"),
            rateLimitReachedType: nil,
            rateLimitResetCredits: ResetCreditsSummary(availableCount: 2, applicableAvailableCount: 2))
        let aliceCredits = ResetCreditsResponse(
            credits: [ResetCredit(id: "1", resetType: "codex_rate_limits", status: "available", isSupportedByPlan: true,
                                  grantedAt: nil, expiresAt: ISO8601.format(now.addingTimeInterval(25 * day)), title: "Full reset"),
                      ResetCredit(id: "2", resetType: "codex_rate_limits", status: "available", isSupportedByPlan: true,
                                  grantedAt: nil, expiresAt: ISO8601.format(now.addingTimeInterval(29 * day)), title: "Full reset")],
            availableCount: 2, totalEarnedCount: 0)
        var aliceEntry = QuotaMonitor.Entry(profile: alice, isActive: true,
                                            snapshot: UsageSnapshot(fetchedAt: now.addingTimeInterval(-70), usage: aliceUsage, resetCredits: aliceCredits),
                                            isStale: false, error: nil, isFetching: false)
        aliceEntry.liveEvent = LiveRateLimitEvent(
            timestamp: now.addingTimeInterval(-40), limitId: "codex", limitName: nil,
            primary: window(used: 39, seconds: 604800, resetIn: 3 * day + 5 * hour, now: now), secondary: nil,
            planType: "pro", limitReached: false)

        // 2. Plus account with both 5h and weekly windows.
        let bob = profile(name: "bob", email: "bob@example.com", plan: "plus", accountId: "acct-bob-0002",
                          subscriptionUntil: now.addingTimeInterval(12 * day), tokenExpiry: now.addingTimeInterval(6 * day),
                          lastRefresh: now.addingTimeInterval(-4 * day))
        let bobUsage = UsageResponse(
            userId: nil, accountId: "acct-bob-0002", email: "bob@example.com", planType: "plus",
            rateLimit: RateLimit(allowed: true, limitReached: false,
                                 primaryWindow: window(used: 12, seconds: 18000, resetIn: 3 * hour + 20 * 60, now: now),
                                 secondaryWindow: window(used: 65, seconds: 604800, resetIn: 2 * day + 9 * hour, now: now)),
            additionalRateLimits: [], credits: nil, rateLimitReachedType: nil,
            rateLimitResetCredits: ResetCreditsSummary(availableCount: 1, applicableAvailableCount: 1))
        let bobEntry = QuotaMonitor.Entry(profile: bob, isActive: false,
                                          snapshot: UsageSnapshot(fetchedAt: now.addingTimeInterval(-20), usage: bobUsage, resetCredits: nil),
                                          isStale: false, error: nil, isFetching: false)

        // 3. Free account that hit its 30-day limit.
        let carol = profile(name: "carol", email: "carol@example.com", plan: "free", accountId: "acct-carol-0003",
                            subscriptionUntil: nil, tokenExpiry: now.addingTimeInterval(9 * day),
                            lastRefresh: now.addingTimeInterval(-1 * day))
        let carolUsage = UsageResponse(
            userId: nil, accountId: "acct-carol-0003", email: "carol@example.com", planType: "free",
            rateLimit: RateLimit(allowed: false, limitReached: true,
                                 primaryWindow: window(used: 100, seconds: 2592000, resetIn: 26 * day, now: now),
                                 secondaryWindow: nil),
            additionalRateLimits: [], credits: nil,
            rateLimitReachedType: RateLimitReachedType(type: "rate_limit_reached", details: "default"),
            rateLimitResetCredits: ResetCreditsSummary(availableCount: 1, applicableAvailableCount: 1))
        let carolEntry = QuotaMonitor.Entry(profile: carol, isActive: false,
                                            snapshot: UsageSnapshot(fetchedAt: now.addingTimeInterval(-20), usage: carolUsage, resetCredits: nil),
                                            isStale: false, error: nil, isFetching: false)

        // 4. Account whose session was revoked server-side: cached data + error.
        let dave = profile(name: "dave", email: "dave@example.com", plan: "pro", accountId: "acct-dave-0004",
                           subscriptionUntil: now.addingTimeInterval(3 * day), tokenExpiry: now.addingTimeInterval(5 * day),
                           lastRefresh: now.addingTimeInterval(-5 * day))
        let daveUsage = UsageResponse(
            userId: nil, accountId: "acct-dave-0004", email: "dave@example.com", planType: "pro",
            rateLimit: RateLimit(allowed: true, limitReached: false,
                                 primaryWindow: window(used: 71, seconds: 604800, resetIn: 1 * day + 2 * hour, now: now),
                                 secondaryWindow: nil),
            additionalRateLimits: [], credits: nil, rateLimitReachedType: nil,
            rateLimitResetCredits: ResetCreditsSummary(availableCount: 0, applicableAvailableCount: 0))
        let daveEntry = QuotaMonitor.Entry(profile: dave, isActive: false,
                                           snapshot: UsageSnapshot(fetchedAt: now.addingTimeInterval(-6 * hour), usage: daveUsage, resetCredits: nil),
                                           isStale: true, error: L.errUnauthorized("token_expired"), isFetching: false)

        return [aliceEntry, bobEntry, carolEntry, daveEntry]
    }
}
