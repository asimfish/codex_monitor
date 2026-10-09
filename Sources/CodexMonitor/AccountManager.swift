import AppKit
import SwiftUI

@MainActor
final class AccountManagerController: NSObject, NSWindowDelegate {
    static let shared = AccountManagerController()
    private var window: NSWindow?

    func open(monitor: QuotaMonitor) {
        if let window = window {
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }
        let created = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 540, height: 460),
                               styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        let hosting = NSHostingView(rootView: AccountManagerView(monitor: monitor))
        hosting.sizingOptions = []
        created.contentView = hosting
        created.setContentSize(NSSize(width: 540, height: 460))
        created.title = L.manageAccounts
        created.level = .floating
        created.isReleasedWhenClosed = false
        created.delegate = self
        created.minSize = NSSize(width: 480, height: 300)
        window = created
        created.center()
        created.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func windowWillClose(_ notification: Notification) { window = nil }

    func previewIfRequested(monitor: QuotaMonitor) {
        guard DemoData.enabled,
              let path = ProcessInfo.processInfo.environment["CODEX_MONITOR_ACCOUNT_MANAGER_SNAPSHOT"] else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
            self.open(monitor: monitor)
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
                guard let view = self.window?.contentView,
                      let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
                view.cacheDisplay(in: view.bounds, to: bitmap)
                if let data = bitmap.representation(using: .png, properties: [:]) {
                    try? data.write(to: URL(fileURLWithPath: path))
                    NSLog("[CodexMonitor] account manager preview written")
                }
            }
        }
    }
}

private struct AccountManagerView: View {
    @ObservedObject var monitor: QuotaMonitor

    var body: some View {
        let profiles = monitor.storedProfilesForManagement
        VStack(alignment: .leading, spacing: 14) {
            Text(L.manageAccounts).font(.title2.weight(.semibold))
            Text(L.manageAccountsHint).font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 10) {
                    if profiles.isEmpty { Text(L.noStoredAccounts).foregroundStyle(.secondary) }
                    ForEach(profiles) { profile in
                        HStack(alignment: .top, spacing: 12) {
                            VStack(alignment: .leading, spacing: 5) {
                                Text(profile.name).font(.headline).textSelection(.enabled)
                                Text(profile.displayName).font(.caption).textSelection(.enabled)
                                if let other = profiles.first(where: { $0.id != profile.id && ProfileStore.sameLogin($0, profile) }) {
                                    Text(L.duplicateAccount(other.name)).font(.caption).foregroundStyle(.orange)
                                }
                                Text(profile.directory.path).font(.caption2).foregroundStyle(.secondary)
                                    .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                            }
                            Spacer(minLength: 8)
                            Button(L.deleteAccount, role: .destructive) { AccountActions.removeProfile(profile, monitor: monitor) }
                                .controlSize(.small)
                        }
                        .padding(12)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 9))
                    }
                }
            }
            HStack {
                Button(L.refreshNow) { monitor.reloadProfiles() }
                Spacer()
                Button(L.openAccountsFolder) { NSWorkspace.shared.open(monitor.store.accountsRoot) }
            }
        }
        .padding(20)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(nsColor: .windowBackgroundColor))
    }
}
