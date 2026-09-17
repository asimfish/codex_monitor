import Foundation

struct AccountOrder {
    enum Move { case up, down, first, last }
    private static let key = "accountDisplayOrder"
    private static let automaticKey = "accountAutomaticOrder"
    private(set) var automatic: Bool
    private let defaults: UserDefaults
    private(set) var ids: [String]

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        automatic = defaults.object(forKey: Self.automaticKey) as? Bool ?? true
        ids = defaults.stringArray(forKey: Self.key) ?? []
    }

    func sorted(_ available: [String], priorities: [String: Int] = [:]) -> [String] {
        let availableSet = Set(available)
        var seen: Set<String> = []
        let stable = (ids + available).filter { availableSet.contains($0) && seen.insert($0).inserted }
        guard automatic else { return stable }
        return stable.enumerated().sorted {
            let lhs = priorities[$0.element, default: 1]
            let rhs = priorities[$1.element, default: 1]
            return lhs == rhs ? $0.offset < $1.offset : lhs < rhs
        }.map(\.element)
    }

    static func priority(remaining: Double?, expired: Bool, hasError: Bool, limited: Bool) -> Int {
        if expired || hasError { return 3 }
        if limited || remaining == 0 { return 2 }
        if let remaining, remaining > 0 { return 0 }
        return 1
    }

    mutating func useAutomaticOrder() {
        automatic = true
        defaults.set(true, forKey: Self.automaticKey)
    }

    mutating func move(_ id: String, direction: Move, available: [String], pinned: Set<String>, priorities: [String: Int] = [:]) {
        let all = sorted(available, priorities: priorities)
        var movable = all.filter { !pinned.contains($0) }
        guard let source = movable.firstIndex(of: id) else { return }
        let target: Int
        switch direction {
        case .up: target = max(0, source - 1)
        case .down: target = min(movable.count - 1, source + 1)
        case .first: target = 0
        case .last: target = movable.count - 1
        }
        guard source != target else { return }
        movable.insert(movable.remove(at: source), at: target)
        var iterator = movable.makeIterator()
        ids = all.map { pinned.contains($0) ? $0 : iterator.next()! }
        defaults.set(ids, forKey: Self.key)
        automatic = false
        defaults.set(false, forKey: Self.automaticKey)
    }
}
