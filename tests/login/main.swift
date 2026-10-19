import Foundation

@main
struct LoginTests {
    @MainActor static func main() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("cm-native-login-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        var failures = 0
        func check(_ condition: Bool, _ message: String) {
            if !condition { failures += 1; print("FAIL: " + message) }
        }
        func waitFor(_ predicate: () -> Bool) async throws {
            for _ in 0..<300 {
                if predicate() { return }
                try await Task.sleep(nanoseconds: 10_000_000)
            }
            throw NSError(domain: "LoginTests", code: 1, userInfo: [NSLocalizedDescriptionKey: "Timed out"])
        }
        let directory = root.appendingPathComponent("account")
        let session = LoginSession(name: "account", directory: directory,
                                   makeBrowserFlow: { BrowserLoginFlow(port: 0) })
        session.start()
        session.cancel()
        check(FileManager.default.fileExists(atPath: directory.path), "cancel keeps a newly added account directory")
        let authURL = directory.appendingPathComponent("auth.json")
        let original = Data("{\"OPENAI_API_KEY\":\"fabricated-original\"}".utf8)
        try original.write(to: authURL)

        var exchange: CheckedContinuation<TokenRefreshResponse, Error>?
        let racing = LoginSession(name: "account", directory: directory, makeBrowserFlow: {
            BrowserLoginFlow(port: 0, exchangeTokens: { _ in
                try await withCheckedThrowingContinuation { exchange = $0 }
            })
        })
        racing.start()
        racing.handleCallback(.success(["state": racing.debugState!, "code": "fake-code"]))
        try await waitFor { exchange != nil }
        racing.cancel()
        exchange!.resume(returning: try JSONDecoder().decode(TokenRefreshResponse.self, from: Data("{\"id_token\":\"fake-id\",\"access_token\":\"fake-access\"}".utf8)))
        try await Task.sleep(nanoseconds: 50_000_000)
        check((try Data(contentsOf: authURL)) == original, "cancelled token exchange cannot overwrite existing credentials")
        check(racing.phase == .cancelled, "late exchange cannot change cancelled state")

        var locator: CheckedContinuation<String?, Never>?
        let marker = root.appendingPathComponent("executed")
        let executable = root.appendingPathComponent("fake-codex")
        try "#!/bin/sh\ntouch '\(marker.path)'\n".write(to: executable, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: executable.path)
        let resolving = LoginSession(name: "resolving", directory: root.appendingPathComponent("resolving"), mode: .deviceCode,
                                     locateCodex: { await withCheckedContinuation { locator = $0 } })
        resolving.start()
        try await waitFor { locator != nil }
        resolving.cancel()
        locator!.resume(returning: executable.path)
        try await Task.sleep(nanoseconds: 50_000_000)
        check(!FileManager.default.fileExists(atPath: marker.path), "cancelled locator cannot launch an old device login")

        for status in [1, 0] {
            let device = root.appendingPathComponent("device-\(status)")
            try "#!/bin/sh\nprintf '%s' '{\"OPENAI_API_KEY\":\"fabricated-new\"}' > \"$CODEX_HOME/auth.json\"\nexit \(status)\n".write(to: executable, atomically: true, encoding: .utf8)
            try FileManager.default.createDirectory(at: device, withIntermediateDirectories: true)
            let destination = device.appendingPathComponent("auth.json")
            try original.write(to: destination)
            let login = LoginSession(name: "device", directory: device, mode: .deviceCode,
                                     locateCodex: { executable.path })
            login.start()
            try await waitFor { !login.isRunning }
            let written = try Data(contentsOf: destination)
            check(status == 0 ? written != original : written == original, "device login publishes only on exit zero")
            if status == 1, case .success = login.phase { check(false, "failed device relogin cannot report old credentials as success") }
        }
        check(!(try FileManager.default.contentsOfDirectory(atPath: root.path)).contains { $0.hasPrefix(".codex-login-") }, "device staging cleaned up")
        guard failures == 0 else { exit(1) }
        print("Native login checks passed: cancelled placeholders, late exchange/locator, staged device failure and success.")
    }
}
