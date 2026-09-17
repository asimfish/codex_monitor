import Foundation

// Sandboxed checks for ProfileStore / Identity / ISO8601 / Fmt.
// Compiled together with Models.swift, ProfileStore.swift and Strings.swift by run_store_tests.sh.
// CODEX_HOME and CODEX_ACCOUNTS_DIR are pointed at a temp dir by the runner script.

var failures = 0
func check(_ cond: Bool, _ msg: String, line: Int = #line) {
    if !cond {
        failures += 1
        print("FAIL line \(line): \(msg)")
    }
}

func b64(_ obj: [String: Any]) -> String {
    let data = try! JSONSerialization.data(withJSONObject: obj)
    return data.base64EncodedString().replacingOccurrences(of: "+", with: "-")
        .replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
}

func fakeJWT(_ payload: [String: Any]) -> String {
    "\(b64(["alg": "RS256"])).\(b64(payload)).sig"
}

func makeAuth(email: String, account: String, lastRefresh: Date, plan: String = "pro", extra: [String: Any] = [:]) -> [String: Any] {
    let now = Date()
    let authClaims: [String: Any] = [
        "chatgpt_account_id": account,
        "chatgpt_plan_type": plan,
        "chatgpt_subscription_active_until": "2026-09-06T12:52:19+00:00",
        "chatgpt_subscription_last_checked": "2026-09-05T07:52:29.874302+00:00",
    ]
    let idToken = fakeJWT(["email": email, "exp": Int(now.timeIntervalSince1970) + 3600, "https://api.openai.com/auth": authClaims])
    let accessToken = fakeJWT(["exp": Int(now.timeIntervalSince1970) + 9 * 86400, "iat": Int(now.timeIntervalSince1970),
                               "https://api.openai.com/auth": authClaims,
                               "https://api.openai.com/profile": ["email": email]])
    var root: [String: Any] = [
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": NSNull(),
        "tokens": ["id_token": idToken, "access_token": accessToken, "refresh_token": "rt.fake.\(account.prefix(4))", "account_id": account],
        "last_refresh": ISO8601.format(lastRefresh),
    ]
    for (k, v) in extra { root[k] = v }
    return root
}

func write(_ obj: [String: Any], to url: URL) {
    try! FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
    let data = try! JSONSerialization.data(withJSONObject: obj, options: [.prettyPrinted, .sortedKeys])
    try! data.write(to: url)
}

func accountId(at url: URL) -> String? {
    (try? AuthFile.load(from: url))?.tokens?.accountId
}

let store = ProfileStore.shared
let fm = FileManager.default
let accA = "aaaaaaaa-0000-0000-0000-000000000001"
let accB = "bbbbbbbb-0000-0000-0000-000000000002"
let accC = "cccccccc-0000-0000-0000-000000000003"
let now = Date()

// --- parsing helpers ----------------------------------------------------------
check(ISO8601.parse("2026-09-05T07:52:32.639755Z") != nil, "parse 6-digit fraction with Z")
check(ISO8601.parse("2026-09-06T12:52:19+00:00") != nil, "parse offset without fraction")
check(ISO8601.parse("2026-09-06T12:52:19.1Z") != nil, "parse 1-digit fraction")
if let d1 = ISO8601.parse("2026-09-05T07:52:32.639755Z"), let d2 = ISO8601.parse("2026-09-05T07:52:32Z") {
    check(abs(d1.timeIntervalSince(d2) - 0.639) < 0.01, "fraction preserved to ms")
}
check(Fmt.windowLabel(seconds: 18000) == L.window5h, "5h label")
check(Fmt.windowLabel(seconds: 604800) == L.windowWeekly, "weekly label")
check(Fmt.percent(56) == "56%", "percent int")
check(Fmt.percent(56.5) == "56.5%", "percent frac")
check(store.sanitise("a copy!/x") == "a-copy--x", "sanitise: got \(store.sanitise("a copy!/x"))")

// --- device-login transcript parsing (real codex 0.153 output shape, with ANSI colours) ----
let esc = "\u{1B}"
let sample = "\nWelcome to Codex [v\(esc)[90m0.153.3\(esc)[0m]\n\(esc)[90mOpenAI's command-line coding agent\(esc)[0m\n\n"
    + "Follow these steps to sign in with ChatGPT using device code authorization:\n\n"
    + "1. Open this link in your browser and sign in to your account\n   \(esc)[94mhttps://auth.openai.com/codex/device\(esc)[0m\n\n"
    + "2. Enter this one-time code \(esc)[90m(expires in 15 minutes)\(esc)[0m\n   \(esc)[94mABCD-EFGHJ\(esc)[0m\n\n"
    + "\(esc)[90mContinue only if you started this login in Codex.\(esc)[0m\n"
check(!TextScan.stripANSI(sample).contains(esc), "ANSI sequences stripped")
if let parsed = TextScan.deviceLogin(in: sample) {
    check(parsed.url == "https://auth.openai.com/codex/device", "url parsed: \(parsed.url)")
    check(parsed.code == "ABCD-EFGHJ", "code parsed: \(parsed.code)")
} else {
    check(false, "deviceLogin returned nil for full transcript")
}
let partial = String(sample.prefix(upTo: sample.range(of: "2. Enter")!.lowerBound))
check(TextScan.deviceLogin(in: partial) == nil, "no code yet -> nil")
check(TextScan.deviceLogin(in: "see https://example.com/x and code ZZZZ-YYYYY ok")?.code == "ZZZZ-YYYYY", "plain text parse")

// --- /wham/usage decoding: reached-type as string vs object, null balance -----------
let usageReached = #"{"user_id":"u","account_id":"a","email":"x@example.com","plan_type":"free","rate_limit":{"allowed":false,"limit_reached":true,"primary_window":{"used_percent":100,"limit_window_seconds":2592000,"reset_after_seconds":2248662,"reset_at":1790948741},"secondary_window":null},"additional_rate_limits":[],"credits":{"has_credits":false,"unlimited":false,"overage_limit_reached":false,"balance":null,"approx_local_messages":null},"rate_limit_reached_type":{"type":"rate_limit_reached","details":"default"},"rate_limit_reset_credits":{"available_count":1,"applicable_available_count":1},"rate_limit_upsell":{"banner_type":"free_trial_rate_limit_reached"}}"#
if let u = try? JSONDecoder().decode(UsageResponse.self, from: Data(usageReached.utf8)) {
    check(u.rateLimit?.limitReached == true && u.rateLimit?.primaryWindow?.remainingPercent == 0, "reached usage decoded")
    check(u.rateLimitReachedType?.type == "rate_limit_reached" && u.rateLimitReachedType?.displayDetail == nil, "object reached-type decoded, generic detail hidden")
    check(u.credits?.balance == nil && u.rateLimitResetCredits?.availableCount == 1, "null balance + reset credits")
    check(u.rateLimit?.primaryWindow?.label == L.windowDays(30), "30-day window label: \(String(describing: u.rateLimit?.primaryWindow?.label))")
    let re = try! JSONEncoder().encode(u)
    check((try? JSONDecoder().decode(UsageResponse.self, from: re)) != nil, "re-encodes for the cache and decodes again")
} else {
    check(false, "usage with object rate_limit_reached_type failed to decode")
}
let usageStringType = usageReached.replacingOccurrences(of: #"{"type":"rate_limit_reached","details":"default"}"#, with: #""workspace_owner_usage_limit_reached""#)
check((try? JSONDecoder().decode(UsageResponse.self, from: Data(usageStringType.utf8)))?.rateLimitReachedType?.displayDetail == "workspace_owner_usage_limit_reached", "string reached-type decoded")
let usageNullType = usageReached.replacingOccurrences(of: #"{"type":"rate_limit_reached","details":"default"}"#, with: "null")
check((try? JSONDecoder().decode(UsageResponse.self, from: Data(usageNullType.utf8)))?.rateLimitReachedType == nil, "null reached-type decoded")

// --- rollout token_count events (real codex 0.153 line shape) -----------------------
let rolloutLine = #"{"timestamp":"2026-09-06T12:07:48.582Z","ordinal":41,"type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":1}},"rate_limits":{"limit_id":"codex","limit_name":null,"primary":{"used_percent":4.0,"window_minutes":10080,"resets_at":1789298098},"secondary":null,"credits":{"has_credits":false,"unlimited":false,"balance":"0"},"individual_limit":null,"spend_control_reached":null,"plan_type":"pro","rate_limit_reached_type":null}}}"#
if let ev = RolloutParser.parse(line: rolloutLine) {
    check(ev.isMainLimit, "limit_id codex is the main limit")
    check(ev.primary?.usedPercent == 4.0 && ev.primary?.limitWindowSeconds == 604800, "primary window parsed: \(String(describing: ev.primary))")
    check(ev.primary?.resetAt == 1789298098 && ev.secondary == nil, "resets_at + null secondary")
    check(ev.planType == "pro" && ev.limitReached == false, "plan / not reached")
    check(abs(ev.timestamp.timeIntervalSince1970 - 1788696468.582) < 0.01, "timestamp parsed: \(ev.timestamp.timeIntervalSince1970)")
    check(ev.asRateLimit.windows.count == 1 && ev.asRateLimit.windows[0].remainingPercent == 96, "asRateLimit")
} else {
    check(false, "rollout line did not parse")
}
let sparkLine = rolloutLine.replacingOccurrences(of: #""limit_id":"codex""#, with: #""limit_id":"codex_bengalfox""#)
    .replacingOccurrences(of: #""rate_limit_reached_type":null"#, with: #""rate_limit_reached_type":"rate_limit_reached""#)
if let spark = RolloutParser.parse(line: sparkLine) {
    check(!spark.isMainLimit && spark.limitId == "codex_bengalfox", "per-model limit id")
    check(spark.limitReached, "reached flag from rate_limit_reached_type")
} else { check(false, "spark line did not parse") }
check(RolloutParser.parse(line: #"{"timestamp":"2026-09-06T12:07:48.582Z","type":"event_msg","payload":{"type":"agent_message","message":"hi"}}"#) == nil, "non token_count ignored")
check(RolloutParser.parse(line: "not json token_count rate_limits") == nil, "garbage ignored")

// tailer over a sandbox sessions dir
let sessions = store.codexHome.appendingPathComponent("sessions")
var dayCal = Calendar(identifier: .gregorian); dayCal.timeZone = .current
let dc = dayCal.dateComponents([.year, .month, .day], from: Date())
let dayDir = sessions.appendingPathComponent(String(format: "%04d/%02d/%02d", dc.year!, dc.month!, dc.day!))
try! fm.createDirectory(at: dayDir, withIntermediateDirectories: true)
let rolloutFile = dayDir.appendingPathComponent("rollout-test-1.jsonl")
func line(at ts: String, used: Double) -> String {
    rolloutLine.replacingOccurrences(of: "2026-09-06T12:07:48.582Z", with: ts).replacingOccurrences(of: #""used_percent":4.0"#, with: "\"used_percent\":\(used)")
}
try! (#"{"timestamp":"2026-09-06T12:00:00.000Z","type":"session_meta","payload":{"id":"x"}}"# + "\n" + line(at: "2026-09-06T12:01:00.000Z", used: 10) + "\n")
    .write(to: rolloutFile, atomically: true, encoding: .utf8)
let tailer = RolloutTailer(sessionsDir: sessions)
var latest = tailer.pollLatest()
check(latest.main?.primary?.usedPercent == 10, "first poll picks up existing event: \(String(describing: latest.main?.primary?.usedPercent))")
check(tailer.pollLatest().main == nil, "nothing new on second poll")
// append: a partial line first (no newline), then complete it
let h = try! FileHandle(forWritingTo: rolloutFile)
h.seekToEndOfFile()
let newer = line(at: "2026-09-06T12:05:00.000Z", used: 12)
let split = newer.index(newer.startIndex, offsetBy: 40)
h.write(Data(String(newer[..<split]).utf8))
try? h.close()
check(tailer.pollLatest().main == nil, "partial line not parsed yet")
let h2 = try! FileHandle(forWritingTo: rolloutFile)
h2.seekToEndOfFile()
h2.write(Data((String(newer[split...]) + "\n" + line(at: "2026-09-06T12:04:00.000Z", used: 11) + "\n").utf8))
try? h2.close()
latest = tailer.pollLatest()
check(latest.main?.primary?.usedPercent == 12, "completed line parsed and newest chosen (12 over 11): \(String(describing: latest.main?.primary?.usedPercent))")
// a stale file is ignored
let oldFile = dayDir.appendingPathComponent("rollout-test-old.jsonl")
try! (line(at: "2026-09-06T12:09:00.000Z", used: 50) + "\n").write(to: oldFile, atomically: true, encoding: .utf8)
try! fm.setAttributes([.modificationDate: Date().addingTimeInterval(-3600)], ofItemAtPath: oldFile.path)
check(tailer.pollLatest().main == nil, "file modified an hour ago is ignored")
// a long-lived thread whose rollout lives under an OLD day directory is still picked up
let oldDay = sessions.appendingPathComponent("2026/01/15")
try! fm.createDirectory(at: oldDay, withIntermediateDirectories: true)
let oldThread = oldDay.appendingPathComponent("rollout-2026-01-15T10-00-00-old-thread.jsonl")
try! (line(at: "2026-09-06T12:20:00.000Z", used: 33) + "\n").write(to: oldThread, atomically: true, encoding: .utf8)
let fresh = RolloutTailer(sessionsDir: sessions)
let fromOld = fresh.pollLatest()
check(fromOld.main != nil && [12.0, 33.0].contains(fromOld.main!.primary!.usedPercent), "full scan finds recently modified files in any day directory")
// appended later (between full scans) is still seen because the file is a known candidate
let h4 = try! FileHandle(forWritingTo: oldThread)
h4.seekToEndOfFile()
h4.write(Data((line(at: "2026-09-06T12:30:00.000Z", used: 34) + "\n").utf8))
try? h4.close()
check(fresh.pollLatest().main?.primary?.usedPercent == 34, "appends to an old-directory thread are tailed")

// spark events land in extras
let h3 = try! FileHandle(forWritingTo: rolloutFile)
h3.seekToEndOfFile()
h3.write(Data((sparkLine + "\n").utf8))
try? h3.close()
latest = tailer.pollLatest()
check(latest.main == nil && latest.extras["codex_bengalfox"] != nil, "per-model event routed to extras")

// --- OAuth helpers -----------------------------------------------------------------
let pkce = PKCE()
check((43...128).contains(pkce.verifier.count), "verifier length \(pkce.verifier.count)")
check(pkce.challenge.count == 43, "S256 challenge is 43 chars: \(pkce.challenge.count)")
check(!pkce.challenge.contains("=") && !pkce.challenge.contains("+") && !pkce.challenge.contains("/"), "base64url alphabet")
let state = OAuthCore.randomState()
check(state.count >= 40 && state != OAuthCore.randomState(), "state is long and random")
let authURL = OAuthCore.authorizeURL(challenge: pkce.challenge, state: state)
let authComps = URLComponents(url: authURL, resolvingAgainstBaseURL: false)!
let q = Dictionary(uniqueKeysWithValues: (authComps.queryItems ?? []).map { ($0.name, $0.value ?? "") })
check(authURL.host == "auth.openai.com" && authURL.path == "/oauth/authorize", "authorize endpoint")
check(q["client_id"] == OAuthConfig.clientId && q["redirect_uri"] == "http://localhost:1455/auth/callback", "client + redirect")
check(q["code_challenge"] == pkce.challenge && q["code_challenge_method"] == "S256" && q["state"] == state, "pkce/state params")
check(q["response_type"] == "code" && (q["scope"] ?? "").contains("offline_access"), "response_type/scope")

let req = OAuthCore.parseRequestLine("GET /auth/callback?code=ac_abc%2Fdef&state=\(state)&x=1%202 HTTP/1.1")
check(req?.method == "GET" && req?.path == "/auth/callback", "request line parsed")
check(req?.query["code"] == "ac_abc/def" && req?.query["state"] == state && req?.query["x"] == "1 2", "query decoded: \(String(describing: req?.query))")
check(OAuthCore.parseRequestLine("GET /favicon.ico HTTP/1.1")?.path == "/favicon.ico", "path without query")
check(OAuthCore.parseRequestLine("garbage") == nil, "garbage request line rejected")

let form = String(decoding: OAuthCore.tokenRequestBody(code: "a b&c", verifier: pkce.verifier), as: UTF8.self)
check(form.contains("grant_type=authorization_code") && form.contains("code=a%20b%26c") && form.contains("code_verifier=\(pkce.verifier)"), "form body: \(form.prefix(80))")
check(form.contains("redirect_uri=http%3A%2F%2Flocalhost%3A1455%2Fauth%2Fcallback"), "redirect_uri encoded")

let fakeTokens = TokenRefreshResponse(
    idToken: fakeJWT(["email": "n@example.com", "exp": 1, "https://api.openai.com/auth": ["chatgpt_account_id": accA, "chatgpt_plan_type": "plus"]]),
    accessToken: fakeJWT(["exp": 2]),
    refreshToken: "rt.new"
)
let authData = try! OAuthCore.buildAuthJSON(fakeTokens)
let builtRaw = try! JSONSerialization.jsonObject(with: authData) as! [String: Any]
let built = try! JSONDecoder().decode(AuthFile.self, from: authData)
check(built.authMode == "chatgpt" && built.tokens?.accountId == accA && built.tokens?.refreshToken == "rt.new", "auth.json shape")
check(builtRaw["OPENAI_API_KEY"] is NSNull, "OPENAI_API_KEY is null like codex writes")
check(built.lastRefreshDate != nil, "last_refresh parseable")
check(Identity(auth: built).email == "n@example.com" && Identity(auth: built).planType == "plus", "identity from built auth.json")
do {
    _ = try OAuthCore.buildAuthJSON(TokenRefreshResponse(idToken: nil, accessToken: "x", refreshToken: nil))
    check(false, "missing id_token should throw")
} catch {
    check(true, "")
}

// --- layout: main=A fresh, a=A stale, b=B --------------------------------------
store.ensureDirectories()
write(makeAuth(email: "a@example.com", account: accA, lastRefresh: now), to: store.mainAuthURL)
write(makeAuth(email: "a@example.com", account: accA, lastRefresh: now.addingTimeInterval(-3 * 3600)),
      to: store.accountsRoot.appendingPathComponent("a/auth.json"))
write(makeAuth(email: "b@example.com", account: accB, lastRefresh: now.addingTimeInterval(-86400), plan: "plus"),
      to: store.accountsRoot.appendingPathComponent("b/auth.json"))

var main = store.loadMain()!
var profiles = store.loadProfiles()
check(profiles.map { $0.name } == ["a", "b"], "profiles listed: \(profiles.map { $0.name })")
check(main.identity?.email == "a@example.com", "identity email from id_token")
check(main.identity?.planType == "pro", "plan from claims")
check(main.identity?.subscriptionUntil != nil, "subscription until parsed")
check(main.identity?.accessTokenExpiry != nil && main.identity?.accessTokenExpired == false, "access token expiry parsed / not expired")
check(main.accountId == accA, "account id")

// adopt: main newer than profile a -> copied
let msg = store.adopt(main: main, into: profiles)
check(msg != nil, "adopt returned a message")
let mainData = try! Data(contentsOf: store.mainAuthURL)
let aData = try! Data(contentsOf: store.accountsRoot.appendingPathComponent("a/auth.json"))
check(mainData == aData, "profile a now equals main")
profiles = store.loadProfiles()
check(store.adopt(main: main, into: profiles) == nil, "second adopt is a no-op")

// activate b: main becomes B, no backup because A was archived
let msgs1 = try! store.activate(profiles.first { $0.name == "b" }!, currentMain: main, profiles: profiles)
check(!msgs1.isEmpty, "activate returned messages")
check(accountId(at: store.mainAuthURL) == accB, "main is B after activate")
check(!fm.fileExists(atPath: store.backupDir.path), "no backup for archived account")
var attrs = try! fm.attributesOfItem(atPath: store.mainAuthURL.path)
check((attrs[.posixPermissions] as? NSNumber)?.intValue == 0o600, "main auth.json is 0600")

// main externally replaced with unarchived C, then activate a -> C backed up
write(makeAuth(email: "c@example.com", account: accC, lastRefresh: now), to: store.mainAuthURL)
main = store.loadMain()!
profiles = store.loadProfiles()
_ = try! store.activate(profiles.first { $0.name == "a" }!, currentMain: main, profiles: profiles)
check(accountId(at: store.mainAuthURL) == accA, "main is A after activate a")
let backups = (try? fm.contentsOfDirectory(atPath: store.backupDir.path)) ?? []
check(backups.count == 1 && backups[0].contains("c_at_example.com"), "backup created for C: \(backups)")

// saveMainAsProfile with a messy name
let saved = try! store.saveMainAsProfile(named: "a copy!")
check(saved.name == "a-copy", "sanitised profile name: \(saved.name)")
check(accountId(at: saved.authURL) == accA, "saved profile holds A")
do {
    _ = try store.saveMainAsProfile(named: "a copy!")
    check(false, "duplicate save should throw")
} catch {
    check(true, "")
}

// applyRefreshedTokens preserves unknown keys and bumps last_refresh
let target = store.accountsRoot.appendingPathComponent("b/auth.json")
write(makeAuth(email: "b@example.com", account: accB, lastRefresh: now.addingTimeInterval(-86400), plan: "plus",
               extra: ["custom_key": "keep-me"]), to: target)
let before = try! AuthFile.load(from: target)
let refreshed = TokenRefreshResponse(idToken: fakeJWT(["email": "b@example.com", "exp": 1]),
                                     accessToken: fakeJWT(["exp": 2]), refreshToken: "rt.fake.rotated")
try! store.applyRefreshedTokens(refreshed, to: target)
let after = try! AuthFile.load(from: target)
let afterRaw = try! JSONSerialization.jsonObject(with: Data(contentsOf: target)) as! [String: Any]
check(after.tokens?.refreshToken == "rt.fake.rotated", "refresh token replaced")
check(after.tokens?.accountId == accB, "account id kept")
check((after.lastRefreshDate ?? .distantPast) > (before.lastRefreshDate ?? .distantFuture), "last_refresh bumped")
check(afterRaw["custom_key"] as? String == "keep-me", "unknown key preserved")
check(afterRaw["auth_mode"] as? String == "chatgpt", "auth_mode preserved")

// hidden / underscore dirs are not profiles
write(makeAuth(email: "x@example.com", account: "x", lastRefresh: now), to: store.backupDir.appendingPathComponent("junk/auth.json"))
write(makeAuth(email: "y@example.com", account: "y", lastRefresh: now), to: store.cacheDir.appendingPathComponent("auth.json"))
check(Set(store.loadProfiles().map { $0.name }) == ["a", "a-copy", "b"], "hidden dirs ignored: \(store.loadProfiles().map { $0.name })")

// chatgpt_base_url from config.toml
try! "model = \"x\"\nchatgpt_base_url = \"https://relay.example.com/backend-api\" # comment\n"
    .write(to: store.codexHome.appendingPathComponent("config.toml"), atomically: true, encoding: .utf8)
check(store.chatGPTBaseURL().absoluteString == "https://relay.example.com/backend-api", "base url from config: \(store.chatGPTBaseURL())")

// Duplicate aliases are presentation-only: select a usable, recent credential.
func displayFixture(_ name: String, account: String?, user: String?, email: String?, age: TimeInterval, expired: Bool = false) -> Profile {
    let dir = URL(fileURLWithPath: "/fixture/" + name)
    var p = Profile(id: name, name: name, directory: dir, authURL: dir.appendingPathComponent("auth.json"), isMain: false)
    var identity = Identity(auth: try! JSONDecoder().decode(AuthFile.self, from: JSONSerialization.data(withJSONObject: makeAuth(email: email ?? "", account: account ?? "", lastRefresh: Date()))))
    identity.accountId = account
    identity.userId = user
    identity.email = email
    identity.accessTokenExpiry = Date().addingTimeInterval(expired ? -100 : 3600)
    p.identity = identity
    p.modified = Date(timeIntervalSince1970: age)
    return p
}
let aliasOld = displayFixture("a", account: "workspace", user: "user", email: "a@example.com", age: 10)
let aliasNew = displayFixture("b", account: "workspace", user: "user", email: "a@example.com", age: 20)
let expiredAlias = displayFixture("c", account: "workspace", user: "user", email: "a@example.com", age: 30, expired: true)
check(ProfileStore.displayProfiles([aliasOld, aliasNew, expiredAlias]).map(\.id) == ["b"], "duplicate aliases select newest unexpired credential")
let otherWorkspace = displayFixture("d", account: "other", user: "user", email: "a@example.com", age: 10)
let otherUser = displayFixture("e", account: "workspace", user: "different", email: "b@example.com", age: 10)
check(ProfileStore.displayProfiles([aliasOld, otherWorkspace, otherUser]).count == 3, "preserve different workspaces and users")
let unknown1 = displayFixture("f", account: nil, user: nil, email: nil, age: 10)
let unknown2 = displayFixture("g", account: nil, user: nil, email: nil, age: 10)
check(ProfileStore.displayProfiles([unknown1, unknown2]).count == 2, "unknown identities remain separate")
check(ProfileStore.displayProfiles([aliasNew, aliasOld]).map(\.id) == ["b"], "selection independent of input order")
check(ProfileStore.displayProfiles([]).isEmpty, "empty display list")

if failures == 0 {
    print("ProfileStore sandbox test: all checks passed")
    exit(0)
} else {
    print("\(failures) check(s) failed")
    exit(1)
}
