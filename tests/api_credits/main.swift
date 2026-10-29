import Foundation

@main struct ApiCreditTests {
    @MainActor static func main() async throws {
        let command = URL(fileURLWithPath: ProcessInfo.processInfo.environment["CM_TEST_CLI"]!)
        let model = ApiCreditModel(command: command)
        await model.run()
        precondition(model.error == nil && model.accounts.isEmpty, "empty isolated storage")
        let now = Date()
        let body: [String: Any] = ["label": "Native API fixture", "organization": "org-native-test", "grant_total": "100.50",
                                   "period_start": ISO8601.format(now.addingTimeInterval(-86400)),
                                   "period_end": ISO8601.format(now.addingTimeInterval(86400)), "admin_key": ""]
        await model.run("save", body: body)
        precondition(model.error == nil, "native input is accepted by real Python validation")
        let account = model.accounts[0]
        precondition(account.label == "Native API fixture" && account.grantTotal == "100.50")
        precondition(!account.hasAdminKey && account.costs == nil && account.estimatedRemaining == nil)
        let reloaded = ApiCreditModel(command: command)
        await reloaded.run()
        precondition(reloaded.accounts.count == 1 && reloaded.accounts[0].id == account.id, "public snapshot persists")
        await model.run("remove", id: account.id)
        precondition(model.error == nil && model.accounts.isEmpty, "native removal changes only API records")
        await model.run("connect", body: ["admin_key": "sk-proj-fabricated-rejected"])
        precondition(model.error != nil && model.accounts.isEmpty, "failed connection never creates an invisible account")
        await model.run("save", body: ["organization": "org-simple-native"])
        precondition(model.error == nil && model.accounts[0].periodMode == "calendar_month")
        precondition(model.accounts[0].grantTotal == nil && model.accounts[0].estimatedRemaining == nil)
        await model.run("remove", id: model.accounts[0].id)
        print("Native API bridge passed: real CLI stdin save, sanitized snapshot decode, persistence and remove.")
    }
}
