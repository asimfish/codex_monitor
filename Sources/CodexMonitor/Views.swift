import AppKit
import SwiftUI

// MARK: - Root (measures its own ideal size so the panel can follow)

private struct SizePreferenceKey: PreferenceKey {
    static var defaultValue: CGSize = .zero
    static func reduce(value: inout CGSize, nextValue: () -> CGSize) { value = nextValue() }
}

private struct SectionHeightPreferenceKey: PreferenceKey {
    static var defaultValue: [String: CGFloat] = [:]
    static func reduce(value: inout [String: CGFloat], nextValue: () -> [String: CGFloat]) {
        value.merge(nextValue(), uniquingKeysWith: { _, new in new })
    }
}

private extension View {
    func measureSectionHeight(_ section: String) -> some View {
        background(GeometryReader { geo in
            Color.clear.preference(key: SectionHeightPreferenceKey.self, value: [section: geo.size.height])
        })
    }
}

struct WidgetRootView: View {
    @ObservedObject var monitor: QuotaMonitor
    @ObservedObject var controller: PanelController
    var onSizeChange: (CGSize) -> Void

    var body: some View {
        VStack(spacing: 0) {
            Group {
                if controller.isCollapsed {
                    CollapsedStripView(monitor: monitor, controller: controller)
                } else {
                    WidgetView(monitor: monitor, controller: controller)
                }
            }
            .frame(width: controller.currentWidth)
            .fixedSize(horizontal: false, vertical: true)
            .background(GeometryReader { geo in
                Color.clear.preference(key: SizePreferenceKey.self, value: geo.size)
            })
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .onPreferenceChange(SizePreferenceKey.self) { onSizeChange($0) }
    }
}

// MARK: - Shared per-entry presentation

extension QuotaMonitor.Entry {
    var statusColor: Color {
        if error != nil { return .red }
        if snapshot == nil && liveEvent == nil { return .gray }
        if isStale && !showsLive { return .yellow }
        if let w = tightestWindow { return quotaColor(remaining: w.remainingPercent) }
        return .green
    }

    var planLabel: String {
        Fmt.planLabel(snapshot?.usage.planType ?? liveEvent?.planType ?? profile.identity?.planType)
    }
}

struct PlanBadge: View {
    let text: String
    var small = false

    var body: some View {
        Text(text)
            .font(.system(size: small ? 9 : 10, weight: .bold))
            .padding(.horizontal, small ? 4 : 5)
            .padding(.vertical, small ? 1 : 2)
            .background(Capsule().fill(Color.primary.opacity(0.10)))
    }
}

// MARK: - Collapsed vertical strip (active account only)

struct CollapsedStripView: View {
    @ObservedObject var monitor: QuotaMonitor
    @ObservedObject var controller: PanelController

    var body: some View {
        let _ = monitor.clockTick
        let entry = monitor.activeEntry
        let window = entry?.tightestWindow
        // Everything except the bottom chevron is plain content, so clicking/dragging the strip
        // only moves the window; expanding takes a deliberate click on the arrow.
        VStack(spacing: 6) {
            Image(systemName: "line.3.horizontal")
                .font(.system(size: 9))
                .foregroundStyle(.tertiary)
            Circle().fill(entry?.statusColor ?? .gray).frame(width: 8, height: 8)
            VerticalQuotaBar(
                fraction: (window?.remainingPercent ?? 0) / 100,
                color: quotaColor(remaining: window?.remainingPercent ?? 100),
                indeterminate: window == nil
            )
            .frame(width: 8, height: 64)
            if let w = window {
                Text(Fmt.percent(w.remainingPercent))
                    .font(.system(size: 10, weight: .bold, design: .rounded))
                    .monospacedDigit()
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
                if let d = w.resetDate {
                    Text(Fmt.shortCountdown(to: d))
                        .font(.system(size: 9))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.7)
                }
            } else if entry?.error != nil {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.system(size: 10))
                    .foregroundStyle(.orange)
            } else if entry == nil {
                Text(L.dash).font(.system(size: 10, weight: .bold))
            } else {
                ProgressView().controlSize(.mini)
            }
            Button {
                controller.isCollapsed = false
            } label: {
                Image(systemName: "chevron.left.circle.fill")
                    .font(.system(size: 15))
                    .foregroundStyle(.secondary)
                    .padding(.top, 2)
            }
            .buttonStyle(.plain)
            .help(entry.map { "\($0.profile.displayName) · \(L.expandPanel)" } ?? L.expandPanel)
        }
        .frame(maxWidth: .infinity)
        .help(L.dragHint)
        .padding(.vertical, 8)
        .padding(.horizontal, 4)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .strokeBorder(Color.white.opacity(0.14), lineWidth: 1)
        )
        .padding(2)
    }
}

struct VerticalQuotaBar: View {
    let fraction: Double
    let color: Color
    var indeterminate = false

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .bottom) {
                Capsule().fill(Color.primary.opacity(0.10))
                if !indeterminate {
                    Capsule()
                        .fill(color.gradient)
                        .frame(height: max(0, min(1, fraction)) * geo.size.height)
                }
            }
        }
    }
}

// MARK: - Per-account actions (copy / export auth.json)

@MainActor
enum AccountActions {
    static func copyContents(_ entry: QuotaMonitor.Entry, monitor: QuotaMonitor) {
        do {
            let data = try Data(contentsOf: entry.profile.authURL)
            let text = String(decoding: data, as: UTF8.self)
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(text, forType: .string)
            monitor.note(L.copiedAuth(entry.profile.displayName))
        } catch {
            monitor.note(L.copyFailed(error.localizedDescription))
        }
    }

    static func copyPath(_ entry: QuotaMonitor.Entry, monitor: QuotaMonitor) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(entry.profile.authURL.path, forType: .string)
        monitor.note(L.copiedPath(entry.profile.authURL.path))
    }

    static func revealInFinder(_ entry: QuotaMonitor.Entry) {
        NSWorkspace.shared.activateFileViewerSelecting([entry.profile.authURL])
    }

    static func export(_ entry: QuotaMonitor.Entry, monitor: QuotaMonitor) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "auth-\(entry.profile.isMain ? "main" : entry.profile.name).json"
        panel.canCreateDirectories = true
        panel.title = L.exportAuth
        NSApp.activate(ignoringOtherApps: true)
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            let data = try Data(contentsOf: entry.profile.authURL)
            try monitor.store.writeAtomically(data, to: url)
            monitor.note(L.exportedAuth(entry.profile.displayName, url.path))
        } catch {
            monitor.note(L.copyFailed(error.localizedDescription))
        }
    }
}

struct AccountActionsMenu: View {
    let entry: QuotaMonitor.Entry
    @ObservedObject var monitor: QuotaMonitor

    var body: some View {
        Menu {
            Button(L.copyAuthJSON) { AccountActions.copyContents(entry, monitor: monitor) }
            Button(L.copyAuthPath) { AccountActions.copyPath(entry, monitor: monitor) }
            Button(L.revealInFinder) { AccountActions.revealInFinder(entry) }
            Divider()
            Button(L.exportAuth) { AccountActions.export(entry, monitor: monitor) }
            Divider()
            Button(L.reloginEllipsis) {
                LoginWindowController.shared.beginRelogin(entry: entry, monitor: monitor, panel: PanelController.shared)
            }
            Divider()
            Button(L.logoutAction, role: .destructive) {
                if PanelController.shared.confirm(
                    title: L.logoutTitle(entry.profile.displayName),
                    message: L.logoutMessage,
                    okTitle: L.logoutAction, destructive: true
                ) {
                    monitor.signOut(entryId: entry.id)
                }
            }
        } label: {
            Image(systemName: "doc.on.doc")
                .font(.caption)
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .help(L.accountActionsHelp)
    }
}

// MARK: - One-line row for a non-active account (click to expand)

struct CompactAccountRow: View {
    let entry: QuotaMonitor.Entry
    @ObservedObject var monitor: QuotaMonitor
    var onExpand: () -> Void
    var onSwitch: () -> Void

    var body: some View {
        HStack(spacing: 6) {
            Circle().fill(entry.statusColor).frame(width: 8, height: 8)
            Text(entry.profile.displayName)
                .font(.caption.weight(.medium))
                .lineLimit(1)
                .truncationMode(.middle)
            PlanBadge(text: entry.planLabel, small: true)
            Spacer(minLength: 4)
            if let w = entry.tightestWindow {
                Text(L.remaining(Fmt.percent(w.remainingPercent)))
                    .font(.caption.weight(.semibold))
                    .monospacedDigit()
                    .foregroundStyle(quotaColor(remaining: w.remainingPercent))
            } else if entry.error != nil {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.caption2)
                    .foregroundStyle(.orange)
            } else if entry.isFetching || entry.snapshot == nil {
                ProgressView().controlSize(.mini)
            }
            AccountActionsMenu(entry: entry, monitor: monitor)
            Button(L.switchBtn) { onSwitch() }
                .buttonStyle(.bordered)
                .controlSize(.mini)
                .help(L.switchHelp)
            Image(systemName: "chevron.right")
                .font(.caption2.weight(.semibold))
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Color.primary.opacity(0.05)))
        .contentShape(Rectangle())
        .onTapGesture { onExpand() }
        .help(L.expandCard)
    }
}

// MARK: - Widget

struct WidgetView: View {
    @ObservedObject var monitor: QuotaMonitor
    @ObservedObject var controller: PanelController
    @State private var launchAtLogin = LaunchAtLogin.isEnabled
    @State private var showLog = false
    @State private var isReordering = false
    @State private var sectionHeights: [String: CGFloat] = [:]
    /// Non-active accounts the user has expanded; the active one is always a full card.
    @State private var expanded: Set<String> = []
    /// Remembered "show all other accounts expanded" preference (on by default; the
    /// collapsed strip is the compact view, so the panel itself shows everything).
    @AppStorage("othersExpandedByDefault") private var othersExpandedByDefault = true

    private var otherIds: [String] { monitor.orderedOtherEntries.map { $0.id } }
    private var allOthersExpanded: Bool { !otherIds.isEmpty && Set(otherIds).isSubset(of: expanded) }

    var body: some View {
        let _ = monitor.clockTick
        VStack(alignment: .leading, spacing: 8) {
            VStack(alignment: .leading, spacing: 8) {
                header
                if monitor.entries.isEmpty {
                    emptyState
                } else {
                    ForEach(monitor.entries.filter { $0.isActive }) { entry in
                        AccountCard(entry: entry, monitor: monitor, controller: controller, onCollapse: nil)
                    }
                    if !otherIds.isEmpty { othersHeader }
                }
            }
            .measureSectionHeight("top")

            if !otherIds.isEmpty {
                ScrollView(.vertical) {
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(monitor.orderedOtherEntries) { entry in
                            VStack(alignment: .leading, spacing: 4) {
                                if isReordering { reorderControls(for: entry) }
                                if expanded.contains(entry.id) {
                                    AccountCard(
                                        entry: entry, monitor: monitor, controller: controller,
                                        onCollapse: { expanded.remove(entry.id) }
                                    )
                                } else {
                                    CompactAccountRow(
                                        entry: entry,
                                        monitor: monitor,
                                        onExpand: { expanded.insert(entry.id) },
                                        onSwitch: { confirmSwitch(to: entry) }
                                    )
                                }
                            }
                        }
                    }
                    .padding(.trailing, 12)
                    .fixedSize(horizontal: false, vertical: true)
                    .measureSectionHeight("others")
                }
                .scrollIndicators(.visible)
                .frame(height: min(sectionHeights["others", default: 0], otherAccountsMaxHeight))
            }

            VStack(alignment: .leading, spacing: 8) {
                footer
                if showLog { logView }
            }
            .measureSectionHeight("bottom")
        }
        .onPreferenceChange(SectionHeightPreferenceKey.self) { sectionHeights = $0 }
        .onAppear {
            if othersExpandedByDefault { expanded.formUnion(otherIds) }
        }
        .onChange(of: otherIds) { previous, ids in
            expanded.formIntersection(ids)
            if othersExpandedByDefault { expanded.formUnion(Set(ids).subtracting(previous)) }
        }
        .padding(14)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .strokeBorder(Color.white.opacity(0.14), lineWidth: 1)
        )
        .padding(2)
    }

    private var otherAccountsMaxHeight: CGFloat {
        // Reserve the fixed sections, 32 pt outer padding and two 8 pt gaps.
        max(80, min(420, controller.maximumHeight
            - sectionHeights["top", default: 0]
            - sectionHeights["bottom", default: 0] - 48))
    }

    private var header: some View {
        HStack(spacing: 8) {
            Image(systemName: "terminal.fill")
                .foregroundStyle(.secondary)
            Text(L.appTitle)
                .font(.system(size: 14, weight: .semibold))
            Spacer()
            if let t = monitor.lastRefresh {
                Text(Fmt.ago(t))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Button {
                Task { await monitor.refreshAll() }
            } label: {
                if monitor.isRefreshing {
                    ProgressView().controlSize(.small).frame(width: 16, height: 16)
                } else {
                    Image(systemName: "arrow.clockwise")
                }
            }
            .buttonStyle(.plain)
            .help(L.refreshNow)

            Menu {
                settingsMenu
            } label: {
                Image(systemName: "ellipsis.circle")
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
            .help(L.settings)

            Button {
                controller.isCollapsed = true
            } label: {
                Image(systemName: "chevron.up.circle").foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
            .help(L.collapsePanel)

            Button {
                controller.hide()
            } label: {
                Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
            .help(L.hidePanelHelp)
        }
    }

    private var othersHeader: some View {
        HStack {
            Text(L.otherAccounts(otherIds.count))
                .font(.caption2)
                .foregroundStyle(.secondary)
            Spacer()
            if !monitor.usesAutomaticAccountOrder {
                Button(L.automaticAccountOrder) { monitor.restoreAutomaticAccountOrder() }
                    .buttonStyle(.link)
                    .font(.caption)
                    .help(L.automaticAccountOrderHelp)
            }
            Button(isReordering ? L.finishOrdering : L.reorderAccounts) {
                isReordering.toggle()
            }
            .buttonStyle(.link)
            .font(.caption)
            Button(allOthersExpanded ? L.collapseAll : L.expandAll) {
                if allOthersExpanded {
                    expanded.subtract(otherIds)
                    othersExpandedByDefault = false
                } else {
                    expanded.formUnion(otherIds)
                    othersExpandedByDefault = true
                }
            }
            .buttonStyle(.link)
            .font(.caption)
        }
        .padding(.horizontal, 4)
    }

    private func reorderControls(for entry: QuotaMonitor.Entry) -> some View {
        HStack(spacing: 12) {
            Text(L.accountPosition((otherIds.firstIndex(of: entry.id) ?? 0) + 1))
                .font(.caption2)
                .foregroundStyle(.secondary)
            Spacer()
            Button { monitor.moveAccount(entry.id, direction: .first) } label: {
                Image(systemName: "arrow.up.to.line")
            }
            .help(L.moveAccountFirst)
            .accessibilityLabel(L.moveAccountFirst)
            .disabled(otherIds.first == entry.id)
            Button { monitor.moveAccount(entry.id, direction: .up) } label: {
                Image(systemName: "arrow.up")
            }
            .help(L.moveAccountUp)
            .accessibilityLabel(L.moveAccountUp)
            .disabled(otherIds.first == entry.id)
            Button { monitor.moveAccount(entry.id, direction: .down) } label: {
                Image(systemName: "arrow.down")
            }
            .help(L.moveAccountDown)
            .accessibilityLabel(L.moveAccountDown)
            .disabled(otherIds.last == entry.id)
            Button { monitor.moveAccount(entry.id, direction: .last) } label: {
                Image(systemName: "arrow.down.to.line")
            }
            .help(L.moveAccountLast)
            .accessibilityLabel(L.moveAccountLast)
            .disabled(otherIds.last == entry.id)
        }
        .buttonStyle(.plain)
        .font(.caption)
        .padding(.horizontal, 10)
    }

    private func confirmSwitch(to entry: QuotaMonitor.Entry) {
        let ok = controller.confirm(
            title: L.switchConfirmTitle(entry.profile.displayName),
            message: L.switchConfirmMessage,
            okTitle: L.switchAction
        )
        if ok { monitor.activate(entryId: entry.id) }
    }

    @ViewBuilder
    private var settingsMenu: some View {
        Picker(L.refreshInterval, selection: $monitor.refreshIntervalSeconds) {
            ForEach(QuotaMonitor.intervalChoices, id: \.self) { s in
                Text(L.intervalLabel(seconds: s)).tag(s)
            }
        }
        Picker(L.windowLevel, selection: $controller.mode) {
            ForEach(WindowMode.allCases) { mode in
                Text(mode.title).tag(mode)
            }
        }
        Toggle(L.launchAtLogin, isOn: Binding(
            get: { launchAtLogin },
            set: { on in setLaunchAtLogin(on) }
        ))
        Toggle(L.autoRefreshToggle, isOn: $monitor.autoRefreshOn401)
        Divider()
        Button(L.addAccountEllipsis) { addAccount() }
        Button(L.openAccountsFolder) { NSWorkspace.shared.open(monitor.store.accountsRoot) }
        Button(L.openUsagePage) {
            if let u = URL(string: "https://chatgpt.com/codex/settings/usage") { NSWorkspace.shared.open(u) }
        }
        Button(showLog ? L.hideLog : L.showLog) { showLog.toggle() }
        Divider()
        Button(L.quit) { NSApp.terminate(nil) }
    }

    private var emptyState: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(L.emptyTitle)
                .font(.subheadline.weight(.medium))
            Text(L.emptyBody)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color.primary.opacity(0.05)))
    }

    private var footer: some View {
        HStack {
            Text(L.accountsCount(monitor.entries.count, abbreviatedHome(monitor.store.accountsRoot.path)))
                .font(.caption2)
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .truncationMode(.middle)
            Spacer()
            Button {
                addAccount()
            } label: {
                Label(L.addAccount, systemImage: "plus.circle")
                    .font(.caption)
            }
            .buttonStyle(.link)
        }
    }

    private var logView: some View {
        VStack(alignment: .leading, spacing: 2) {
            ForEach(Array(monitor.recentMessages.prefix(8).enumerated()), id: \.offset) { _, line in
                Text(line)
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if monitor.recentMessages.isEmpty {
                Text(L.noEvents).font(.system(size: 10, design: .monospaced)).foregroundStyle(.secondary)
            }
        }
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(Color.primary.opacity(0.05)))
    }

    private func abbreviatedHome(_ path: String) -> String {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return path.hasPrefix(home) ? "~" + path.dropFirst(home.count) : path
    }

    private func setLaunchAtLogin(_ on: Bool) {
        do {
            if on { try LaunchAtLogin.enable() } else { try LaunchAtLogin.disable() }
            launchAtLogin = LaunchAtLogin.isEnabled
        } catch {
            controller.info(title: L.launchAtLoginFailed, message: error.localizedDescription)
        }
    }

    private func addAccount() {
        LoginWindowController.shared.begin(monitor: monitor, panel: controller)
    }
}

// MARK: - Account card

struct AccountCard: View {
    let entry: QuotaMonitor.Entry
    @ObservedObject var monitor: QuotaMonitor
    @ObservedObject var controller: PanelController
    /// Present for non-active cards; collapses the card back to a one-line row.
    var onCollapse: (() -> Void)? = nil

    private var usage: UsageResponse? { entry.snapshot?.usage }
    private var identity: Identity? { entry.profile.identity }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            titleRow
            quotaSection
            let extras = entry.displayAdditionalLimits
            if !extras.isEmpty {
                extrasSection(extras)
            }
            if let src = entry.dataSource {
                Text(src.live ? L.sourceLive(Fmt.time.string(from: src.at)) : L.sourceAPI(Fmt.time.string(from: src.at)))
                    .font(.system(size: 9))
                    .foregroundStyle(src.live ? Color.green.opacity(0.9) : Color.secondary)
            }
            infoGrid
            if let err = entry.error {
                errorRow(err)
            } else if entry.isStale, let at = entry.snapshot?.fetchedAt {
                Text(L.cachedFrom(Fmt.dateTime.string(from: at)))
                    .font(.caption2)
                    .foregroundStyle(.orange)
            }
        }
        .padding(10)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(entry.isActive ? Color.accentColor.opacity(0.12) : Color.primary.opacity(0.05))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .strokeBorder(entry.isActive ? Color.accentColor.opacity(0.35) : Color.clear, lineWidth: 1)
        )
        .help(entry.profile.authURL.path)
    }

    /// The directory name is only worth showing when it adds information beyond the email.
    private var showsProfileName: Bool {
        guard !entry.profile.isMain else { return false }
        let name = entry.profile.name
        guard name != entry.profile.displayName else { return false }
        if let email = entry.profile.email, let local = email.split(separator: "@").first,
           name.lowercased() == local.lowercased() {
            return false
        }
        return true
    }

    private var titleRow: some View {
        HStack(spacing: 6) {
            Circle().fill(entry.statusColor).frame(width: 8, height: 8)
            Text(entry.profile.displayName)
                .font(.system(size: 13, weight: .semibold))
                .lineLimit(1)
                .truncationMode(.middle)
            PlanBadge(text: entry.planLabel)
            if showsProfileName {
                Text(entry.profile.name)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 4)
            if entry.isFetching {
                ProgressView().controlSize(.mini)
            }
            AccountActionsMenu(entry: entry, monitor: monitor)
            if entry.isActive {
                Text(L.inUse)
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(Color.accentColor)
                if entry.profile.isMain {
                    Button(L.saveAsProfile) { saveAsProfile() }
                        .buttonStyle(.link)
                        .font(.caption2)
                        .help(L.saveAsProfileHelp)
                }
            } else {
                Button(L.switchBtn) { switchTo() }
                    .buttonStyle(.bordered)
                    .controlSize(.mini)
                    .help(L.switchHelp)
                if let collapse = onCollapse {
                    Button { collapse() } label: {
                        Image(systemName: "chevron.up")
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(.secondary)
                    }
                    .buttonStyle(.plain)
                    .help(L.collapseCard)
                }
            }
        }
    }

    @ViewBuilder
    private var quotaSection: some View {
        if let rl = entry.displayRateLimit, !rl.windows.isEmpty {
            ForEach(Array(rl.windows.enumerated()), id: \.offset) { _, w in
                WindowRow(window: w)
            }
            if rl.limitReached == true || rl.allowed == false {
                Text(L.limitReached(entry.showsLive ? nil : usage?.rateLimitReachedType?.displayDetail))
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(.red)
            }
        } else if entry.snapshot == nil && entry.error == nil {
            HStack(spacing: 6) {
                ProgressView().controlSize(.small)
                Text(L.loadingUsage).font(.caption).foregroundStyle(.secondary)
            }
        }
        if let c = usage?.credits, c.hasCredits == true {
            Text(L.creditsBalance(c.unlimited == true ? L.unlimited : (c.balance ?? "?")))
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
    }

    private func extrasSection(_ extras: [AdditionalRateLimit]) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            ForEach(extras.indices, id: \.self) { i in
                let x = extras[i]
                HStack(spacing: 4) {
                    Text(x.limitName ?? x.meteredFeature ?? L.otherLimit)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                    ForEach(Array((x.rateLimit?.windows ?? []).enumerated()), id: \.offset) { _, w in
                        Text("\(w.label) \(L.remainingShort(Fmt.percent(w.remainingPercent)))")
                            .font(.caption2)
                            .monospacedDigit()
                            .padding(.horizontal, 5)
                            .padding(.vertical, 1)
                            .background(Capsule().fill(Color.primary.opacity(0.07)))
                    }
                    Spacer(minLength: 0)
                }
            }
        }
    }

    private var infoGrid: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .top, spacing: 8) {
                infoCell(L.resetCredits, resetCreditsText, tint: nil)
                infoCell(L.subscriptionExpiry, subscriptionText, tint: subscriptionTint)
                    .help(L.subscriptionSnapshotHelp)
            }
            HStack(alignment: .top, spacing: 8) {
                infoCell(L.tokenValidity, tokenExpiryText, tint: tokenTint)
                infoCell(L.lastRefresh, lastRefreshText, tint: nil)
            }
        }
    }

    private func infoCell(_ label: String, _ value: String, tint: Color?) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(label).font(.caption2).foregroundStyle(.secondary)
            Text(value)
                .font(.caption)
                .monospacedDigit()
                .foregroundStyle(tint ?? .primary)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var resetCreditsText: String {
        guard let n = entry.resetCreditCount else { return entry.snapshot == nil ? "..." : L.dash }
        if let exp = entry.snapshot?.resetCredits?.earliestExpiry, n > 0 {
            return L.timesWithExpiry(n, Fmt.dateOnly.string(from: exp))
        }
        return L.times(n)
    }

    /// The id_token claims describe the subscription as it was when the account *logged in*
    /// (`chatgpt_subscription_last_checked`); token refreshes do not re-check it, and no endpoint
    /// reachable with a Codex token returns the live billing period. So: show the snapshot, say
    /// when it was taken, and let the live plan from the API override its meaning.
    private var subscriptionText: String {
        guard let d = identity?.subscriptionUntil else { return L.dash }
        let checked = identity?.subscriptionLastChecked.map { L.checkedAtLogin(Fmt.dateTime.string(from: $0)) } ?? ""
        if d < Date() {
            let plan = (usage?.planType ?? "").lowercased()
            if !plan.isEmpty && plan != "free" { return L.subscriptionRenewedPending }
            return Fmt.dateTime.string(from: d) + L.subscriptionEnded
        }
        return Fmt.dateTime.string(from: d) + L.paren(Fmt.daysLeft(until: d)) + (checked.isEmpty ? "" : "\n" + checked)
    }

    private var subscriptionTint: Color? {
        guard let d = identity?.subscriptionUntil else { return nil }
        if d < Date() {
            let plan = (usage?.planType ?? "").lowercased()
            return (!plan.isEmpty && plan != "free") ? nil : .orange
        }
        return d.timeIntervalSinceNow < 3 * 86400 ? .orange : nil
    }

    /// Access tokens live 10 days and are renewed automatically (by Codex, and by the monitor
    /// after a 401); this is the credential's age, not the account's validity.
    private var tokenExpiryText: String {
        guard let d = identity?.accessTokenExpiry else { return L.dash }
        if d < Date() { return Fmt.dateTime.string(from: d) + L.expired }
        return L.credentialUntil(Fmt.dateTime.string(from: d))
    }

    private var tokenTint: Color? {
        guard let d = identity?.accessTokenExpiry else { return nil }
        return d < Date() ? .red : nil
    }

    private var lastRefreshText: String {
        guard let d = entry.profile.lastRefresh else { return L.dash }
        return Fmt.fullDateTime.string(from: d)
    }

    private func errorRow(_ message: String) -> some View {
        HStack(alignment: .top, spacing: 6) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.orange)
                .font(.caption)
            Text(message)
                .font(.caption2)
                .foregroundStyle(.orange)
                .lineLimit(3)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
            VStack(alignment: .trailing, spacing: 4) {
                if entry.profile.auth?.tokens?.refreshToken != nil {
                    Button(L.refreshTokenBtn) { refreshToken() }
                        .buttonStyle(.bordered)
                        .controlSize(.mini)
                        .help(L.refreshTokenHelp)
                }
                Button(L.reloginEllipsis) {
                    LoginWindowController.shared.beginRelogin(entry: entry, monitor: monitor, panel: controller)
                }
                .buttonStyle(.bordered)
                .controlSize(.mini)
                .help(L.reloginHelp)
            }
        }
    }

    // MARK: Actions

    private func switchTo() {
        let ok = controller.confirm(
            title: L.switchConfirmTitle(entry.profile.displayName),
            message: L.switchConfirmMessage,
            okTitle: L.switchAction
        )
        if ok { monitor.activate(entryId: entry.id) }
    }

    private func refreshToken() {
        let ok = controller.confirm(
            title: L.refreshConfirmTitle(entry.profile.displayName),
            message: L.refreshConfirmMessage,
            okTitle: L.refreshAction,
            destructive: true
        )
        if ok { Task { await monitor.refreshTokens(entryId: entry.id) } }
    }

    private func saveAsProfile() {
        let suggested = monitor.store.suggestedProfileName(for: entry.profile)
        if let name = controller.prompt(title: L.saveProfileTitle, message: L.saveProfileMessage, defaultValue: suggested) {
            monitor.saveMainAsProfile(named: name)
        }
    }
}

// MARK: - Quota bar row

/// Shared colour scale for bars and status dots: >50% green, 20-50% orange, <=20% red.
func quotaColor(remaining r: Double) -> Color {
    if r > 50 { return .green }
    if r > 20 { return .orange }
    return .red
}

struct WindowRow: View {
    let window: RateLimitWindow

    private var color: Color { quotaColor(remaining: window.remainingPercent) }

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 8) {
                Text(window.label)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .frame(width: 44, alignment: .leading)
                QuotaBar(fraction: window.remainingPercent / 100, color: color)
                Text(L.remaining(Fmt.percent(window.remainingPercent)))
                    .font(.system(size: 12, weight: .semibold, design: .rounded))
                    .monospacedDigit()
                    .frame(width: 78, alignment: .trailing)
            }
            if let d = window.resetDate {
                Text(L.resetAt(Fmt.dateTime.string(from: d), Fmt.countdown(to: d)))
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .padding(.leading, 52)
            }
        }
    }
}

struct QuotaBar: View {
    let fraction: Double
    let color: Color

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(Color.primary.opacity(0.10))
                Capsule()
                    .fill(color.gradient)
                    .frame(width: max(0, min(1, fraction)) * geo.size.width)
            }
        }
        .frame(height: 8)
    }
}

// MARK: - Menu bar menu

struct MenuBarMenu: View {
    @ObservedObject var monitor: QuotaMonitor
    @ObservedObject var controller: PanelController
    @State private var launchAtLogin = LaunchAtLogin.isEnabled

    var body: some View {
        if monitor.entries.isEmpty {
            Text(L.noLoginsFound)
        }
        ForEach(monitor.entries) { entry in
            Button {
                guard !entry.isActive else { return }
                if controller.confirm(title: L.switchConfirmTitle(entry.profile.displayName),
                                      message: L.menuSwitchMessage,
                                      okTitle: L.switchAction) {
                    monitor.activate(entryId: entry.id)
                }
            } label: {
                Text(menuLine(entry))
            }
        }
        Divider()
        Button(controller.isVisible ? L.hidePanel : L.showPanel) { controller.toggle() }
        Button(controller.isCollapsed ? L.expandPanel : L.collapsePanel) {
            controller.toggleCollapsed()
            if !controller.isVisible { controller.show() }
        }
        Button(L.refreshNow) { Task { await monitor.refreshAll() } }
        Picker(L.windowLevel, selection: $controller.mode) {
            ForEach(WindowMode.allCases) { mode in
                Text(mode.title).tag(mode)
            }
        }
        Toggle(L.launchAtLogin, isOn: Binding(
            get: { launchAtLogin },
            set: { on in
                do {
                    if on { try LaunchAtLogin.enable() } else { try LaunchAtLogin.disable() }
                } catch {
                    NSLog("[CodexMonitor] launch at login failed: %@", error.localizedDescription)
                }
                launchAtLogin = LaunchAtLogin.isEnabled
            }
        ))
        Button(L.addAccountEllipsis) { LoginWindowController.shared.begin(monitor: monitor, panel: controller) }
        Button(L.openAccountsFolder) { NSWorkspace.shared.open(monitor.store.accountsRoot) }
        Divider()
        Button(L.quit) { NSApp.terminate(nil) }
    }

    private func menuLine(_ e: QuotaMonitor.Entry) -> String {
        var parts: [String] = [(e.isActive ? "* " : "  ") + e.profile.displayName]
        if let w = e.tightestWindow {
            parts.append(L.remaining(Fmt.percent(w.remainingPercent)))
            if let d = w.resetDate { parts.append(L.resetShort(Fmt.dateTime.string(from: d))) }
        } else if e.error != nil {
            parts.append(L.readFailed)
        }
        if let n = e.resetCreditCount { parts.append(L.resetCoupons(n)) }
        return parts.joined(separator: L.separator)
    }
}
