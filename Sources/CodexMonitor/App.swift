import AppKit
import SwiftUI

@main
struct CodexMonitorApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @ObservedObject private var monitor = QuotaMonitor.shared
    @ObservedObject private var controller = PanelController.shared

    var body: some Scene {
        MenuBarExtra {
            MenuBarMenu(monitor: monitor, controller: controller)
        } label: {
            HStack(spacing: 3) {
                Image(systemName: "speedometer")
                Text(monitor.menuBarTitle)
            }
        }
        .menuBarExtraStyle(.menu)
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        let me = NSRunningApplication.current
        let bundleId = Bundle.main.bundleIdentifier ?? "com.codexmonitor.app"
        let others = NSRunningApplication.runningApplications(withBundleIdentifier: bundleId)
            .filter { $0.processIdentifier != me.processIdentifier }
        if !others.isEmpty {
            NSLog("[CodexMonitor] another instance is already running (pid %d); exiting", others[0].processIdentifier)
            NSApp.terminate(nil)
            return
        }
        NSApp.setActivationPolicy(.accessory)
        QuotaMonitor.shared.start()
        PanelController.shared.setup(monitor: QuotaMonitor.shared)
        LoginDebug.runIfRequested()
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        PanelController.shared.show()
        return false
    }

    func applicationSupportsSecureRestorableState(_ app: NSApplication) -> Bool { true }
}
