import AppKit
import Combine
import SwiftUI

/// Borderless, non-activating panel: clicking it never steals focus from the app you are using,
/// but its own buttons still work because it can become key.
final class FloatingPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

enum WindowMode: String, CaseIterable, Identifiable {
    case top      // above every normal window
    case normal   // behaves like a regular window
    case desktop  // pinned just above the wallpaper, below all windows

    var id: String { rawValue }
    var title: String {
        switch self {
        case .top: return L.modeTop
        case .normal: return L.modeNormal
        case .desktop: return L.modeDesktop
        }
    }
    var level: NSWindow.Level {
        switch self {
        case .top: return .floating
        case .normal: return .normal
        case .desktop: return NSWindow.Level(rawValue: Int(CGWindowLevelForKey(.desktopIconWindow)) + 1)
        }
    }
}

@MainActor
final class PanelController: NSObject, ObservableObject, NSWindowDelegate {
    static let shared = PanelController()

    static let panelWidth: CGFloat = 372
    /// Collapsed form is a narrow vertical strip.
    static let stripWidth: CGFloat = 46

    @Published private(set) var isVisible = false
    @Published private(set) var maximumHeight: CGFloat = 700
    @Published var mode: WindowMode {
        didSet {
            UserDefaults.standard.set(mode.rawValue, forKey: "windowMode")
            applyMode()
        }
    }
    /// Collapsed = a single compact strip showing only the active account.
    @Published var isCollapsed: Bool {
        didSet { UserDefaults.standard.set(isCollapsed, forKey: "panelCollapsed") }
    }

    private var panel: FloatingPanel?
    private var lastSize: CGSize = .zero

    private override init() {
        let raw = UserDefaults.standard.string(forKey: "windowMode") ?? WindowMode.top.rawValue
        mode = WindowMode(rawValue: raw) ?? .top
        isCollapsed = UserDefaults.standard.bool(forKey: "panelCollapsed")
        super.init()
    }

    var currentWidth: CGFloat { isCollapsed ? Self.stripWidth : Self.panelWidth }

    func toggleCollapsed() { isCollapsed.toggle() }

    func setup(monitor: QuotaMonitor) {
        guard panel == nil else { return }
        let root = WidgetRootView(monitor: monitor, controller: self) { [weak self] size in
            self?.resize(to: size)
        }
        let hosting = NSHostingView(rootView: root)
        let p = FloatingPanel(
            contentRect: NSRect(x: 0, y: 0, width: Self.panelWidth, height: 240),
            styleMask: [.borderless, .nonactivatingPanel, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        p.isFloatingPanel = true
        p.hidesOnDeactivate = false
        p.isReleasedWhenClosed = false
        p.becomesKeyOnlyIfNeeded = true
        p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        p.backgroundColor = .clear
        p.isOpaque = false
        p.hasShadow = true
        p.isMovableByWindowBackground = true
        p.animationBehavior = .utilityWindow
        p.contentView = hosting
        p.delegate = self
        panel = p
        applyMode()
        restoreOrigin()
        updateMaximumHeight()

        if UserDefaults.standard.object(forKey: "panelVisible") as? Bool ?? true {
            show()
        }
        scheduleDebugSnapshotIfRequested(hosting)
    }

    /// Dev aid: `CODEX_MONITOR_SNAPSHOT=/tmp/x.png` renders the panel content to a PNG after the
    /// first refresh and quits. Lets the UI be checked without screen-recording permission.
    private func scheduleDebugSnapshotIfRequested(_ view: NSView) {
        guard let path = ProcessInfo.processInfo.environment["CODEX_MONITOR_SNAPSHOT"], !path.isEmpty else { return }
        let delay = Double(ProcessInfo.processInfo.environment["CODEX_MONITOR_SNAPSHOT_DELAY"] ?? "") ?? 8
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) {
            let bounds = view.bounds
            if let rep = view.bitmapImageRepForCachingDisplay(in: bounds) {
                view.cacheDisplay(in: bounds, to: rep)
                if let png = rep.representation(using: .png, properties: [:]) {
                    try? png.write(to: URL(fileURLWithPath: path))
                    NSLog("[CodexMonitor] snapshot written to %@ (%.0fx%.0f)", path, bounds.width, bounds.height)
                }
            }
            NSApp.terminate(nil)
        }
    }

    private func applyMode() {
        guard let p = panel else { return }
        p.level = mode.level
        if mode == .desktop {
            p.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle]
        } else {
            p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        }
    }

    // MARK: Visibility

    func show() {
        guard let p = panel else { return }
        let wasVisible = isVisible
        p.orderFrontRegardless()
        isVisible = true
        UserDefaults.standard.set(true, forKey: "panelVisible")
        if !wasVisible {
            Task { await QuotaMonitor.shared.refreshIfStale(seconds: 10) }
        }
    }

    func hide() {
        panel?.orderOut(nil)
        isVisible = false
        UserDefaults.standard.set(false, forKey: "panelVisible")
    }

    func toggle() { isVisible ? hide() : show() }

    // MARK: Geometry

    /// Keeps the top edge fixed, and the horizontal edge nearest to the screen edge fixed, so
    /// collapsing a panel parked at the right of the screen leaves the strip at the right.
    private func resize(to size: CGSize) {
        guard let p = panel, size.width > 0, size.height > 0 else { return }
        let new = CGSize(width: ceil(size.width), height: ceil(size.height))
        guard new != lastSize else { return }
        lastSize = new
        var frame = p.frame
        let topY = frame.maxY
        let oldMinX = frame.minX
        let oldMaxX = frame.maxX
        let screenMidX = (p.screen ?? NSScreen.main)?.frame.midX ?? frame.midX
        let anchorRight = frame.midX >= screenMidX
        frame.size = new
        frame.origin.y = topY - new.height
        frame.origin.x = anchorRight ? oldMaxX - new.width : oldMinX
        if let visible = p.screen?.visibleFrame {
            frame.origin.y = max(visible.minY + 12, min(frame.origin.y, visible.maxY - new.height - 12))
        }
        p.setFrame(frame, display: true, animate: false)
    }

    private func restoreOrigin() {
        guard let p = panel else { return }
        let d = UserDefaults.standard
        if let x = d.object(forKey: "panelOriginX") as? Double, let y = d.object(forKey: "panelOriginY") as? Double {
            let origin = NSPoint(x: x, y: y)
            let probe = NSRect(origin: origin, size: p.frame.size)
            if NSScreen.screens.contains(where: { $0.visibleFrame.intersects(probe) }) {
                p.setFrameOrigin(origin)
                return
            }
        }
        if let screen = NSScreen.main ?? NSScreen.screens.first {
            let vf = screen.visibleFrame
            p.setFrameOrigin(NSPoint(x: vf.maxX - p.frame.width - 24, y: vf.maxY - p.frame.height - 24))
        }
    }

    private func updateMaximumHeight() {
        if let screen = panel?.screen ?? NSScreen.main {
            maximumHeight = screen.visibleFrame.height - 24
        }
    }

    func windowDidChangeScreen(_ notification: Notification) {
        updateMaximumHeight()
    }

    func windowDidChangeScreenProfile(_ notification: Notification) {
        updateMaximumHeight()
    }

    func windowDidMove(_ notification: Notification) {
        guard let p = panel else { return }
        UserDefaults.standard.set(p.frame.origin.x, forKey: "panelOriginX")
        UserDefaults.standard.set(p.frame.origin.y, forKey: "panelOriginY")
    }

    // MARK: Dialogs

    /// Modal confirmation. Activates the app briefly so the alert is actually in front.
    func confirm(title: String, message: String, okTitle: String, destructive: Bool = false) -> Bool {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message
        alert.alertStyle = destructive ? .warning : .informational
        alert.addButton(withTitle: okTitle)
        alert.addButton(withTitle: L.cancel)
        if destructive { alert.buttons.first?.hasDestructiveAction = true }
        NSApp.activate(ignoringOtherApps: true)
        return alert.runModal() == .alertFirstButtonReturn
    }

    func prompt(title: String, message: String, defaultValue: String) -> String? {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message
        alert.addButton(withTitle: L.ok)
        alert.addButton(withTitle: L.cancel)
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 260, height: 24))
        field.stringValue = defaultValue
        alert.accessoryView = field
        alert.window.initialFirstResponder = field
        NSApp.activate(ignoringOtherApps: true)
        guard alert.runModal() == .alertFirstButtonReturn else { return nil }
        let value = field.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        return value.isEmpty ? nil : value
    }

    func info(title: String, message: String) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message
        alert.addButton(withTitle: L.gotIt)
        NSApp.activate(ignoringOtherApps: true)
        alert.runModal()
    }
}
