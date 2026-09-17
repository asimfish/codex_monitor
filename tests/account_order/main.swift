import Foundation

let suite = "CodexMonitor.OrderTests.\(UUID().uuidString)"
let defaults = UserDefaults(suiteName: suite)!
defer { defaults.removePersistentDomain(forName: suite) }
var order = AccountOrder(defaults: defaults)
let accounts = ["alice", "bob", "carol", "dave"]
let pinned: Set<String> = ["alice"]
func expect(_ actual: [String], _ expected: [String], _ message: String) {
    precondition(actual == expected, "\(message): got \(actual), expected \(expected)")
}
expect(order.sorted(accounts), accounts, "default order")
order.move("dave", direction: .first, available: accounts, pinned: pinned)
expect(order.sorted(accounts), ["alice", "dave", "bob", "carol"], "move to top")
expect(AccountOrder(defaults: defaults).sorted(accounts), order.sorted(accounts), "reload saved order")
order.move("dave", direction: .down, available: accounts, pinned: pinned)
expect(order.sorted(accounts), ["alice", "bob", "dave", "carol"], "move down")
order.move("carol", direction: .up, available: accounts, pinned: pinned)
expect(order.sorted(accounts), ["alice", "bob", "carol", "dave"], "move up")
order.move("bob", direction: .last, available: accounts, pinned: pinned)
expect(order.sorted(accounts), ["alice", "carol", "dave", "bob"], "move to bottom")
for (id, move) in [("carol", AccountOrder.Move.up), ("bob", .down), ("alice", .last), ("missing", .first)] {
    order.move(id, direction: move, available: accounts, pinned: pinned)
}
expect(order.sorted(accounts), ["alice", "carol", "dave", "bob"], "boundaries and pinned account")
expect(order.sorted(["bob", "carol", "new"]), ["carol", "bob", "new"], "removed and new accounts")
order.move("bob", direction: .first, available: accounts, pinned: ["carol"])
expect(order.sorted(accounts), ["bob", "carol", "alice", "dave"], "switch active account keeps saved slot")
expect(order.sorted([]), [], "empty list")
order.move("only", direction: .down, available: ["only"], pinned: [])
expect(order.sorted(["only"]), ["only"], "single account")
order.useAutomaticOrder()
let priorities = ["alice": 3, "bob": 2, "carol": 0, "dave": 1]
expect(order.sorted(accounts, priorities: priorities), ["carol", "dave", "bob", "alice"], "usable accounts first")
precondition(AccountOrder(defaults: defaults).automatic, "automatic mode survives reload")
order.move("bob", direction: .up, available: accounts, pinned: [], priorities: priorities)
expect(order.sorted(accounts, priorities: priorities), ["carol", "bob", "dave", "alice"], "manual move starts from visible automatic order")
precondition(!AccountOrder(defaults: defaults).automatic, "manual mode survives reload")
order.useAutomaticOrder()
expect(order.sorted(accounts, priorities: ["alice": 0, "bob": 3, "carol": 2, "dave": 1]),
       ["alice", "dave", "carol", "bob"], "refresh updates automatic order")
precondition(AccountOrder.priority(remaining: 50, expired: false, hasError: false, limited: false) == 0)
precondition(AccountOrder.priority(remaining: 0, expired: false, hasError: false, limited: false) == 2)
precondition(AccountOrder.priority(remaining: 90, expired: true, hasError: false, limited: false) == 3)
precondition(AccountOrder.priority(remaining: 90, expired: false, hasError: true, limited: false) == 3)
precondition(AccountOrder.priority(remaining: 90, expired: false, hasError: false, limited: true) == 2)
precondition(AccountOrder.priority(remaining: nil, expired: false, hasError: false, limited: false) == 1)
print("Account order tests: all checks passed")
