import SwiftUI

struct AntigravityWidgetSection: View {
    @ObservedObject var model: AntigravityModel
    @AppStorage("antigravityExpanded") private var expanded = true
    @State private var collapsedAccounts: Set<UUID> = []
    @State private var systemCollapsed = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            header
            if expanded {
                let active = model.activeAccount
                if let pending = model.systemSwitch { systemSwitchNotice(pending) }
                if let active { savedCard(active, active: true) }
                else { systemCard(active: true) }
                HStack {
                    if !model.accounts.isEmpty {
                        Text("其他账号 · \(model.accounts.count)")
                            .font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 0)
                    Button("添加账号") { model.addAccount() }
                        .buttonStyle(.bordered).controlSize(.mini)
                        .disabled(model.busy || !model.signingIn.isEmpty || DemoData.enabled)
                }.padding(.horizontal, 4)
                if active != nil { systemCard(active: false) }
                ForEach(model.orderedAccounts.filter { $0.id != active?.id }) { savedCard($0, active: false) }
            }
        }.accessibilityIdentifier("antigravity-section")
    }

    private var header: some View {
        HStack(spacing: 8) {
            Button { expanded.toggle() } label: {
                Label("Antigravity", systemImage: expanded ? "chevron.down" : "chevron.right")
                    .font(.caption.weight(.semibold))
            }
            .buttonStyle(.plain)
            Spacer(minLength: 4)
            Button("打开官方客户端") { model.openLogin() }
                .buttonStyle(.link).font(.caption)
                .help("在官方 Antigravity 的账号设置中切换系统登录")
            Button { Task { await model.refresh() } } label: {
                if model.busy { ProgressView().controlSize(.mini) }
                else { Image(systemName: "arrow.clockwise").font(.caption) }
            }
            .buttonStyle(.plain).disabled(model.busy)
            .help("刷新 Antigravity 额度").accessibilityLabel("刷新 Antigravity 额度")
        }.padding(.horizontal, 4)
    }

    private func systemCard(active: Bool) -> some View {
        let status = model.systemStatus
        let open = active || !systemCollapsed
        return VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                accountTitle(status.email ?? "系统账号", status: status, stale: model.systemQuotaIsStale)
                Spacer(minLength: 4)
                accountSwitch(active: active) { model.selectActiveAccount(nil) }
                if !active { detailsToggle(open: open) { systemCollapsed.toggle() } }
            }
            Label("当前系统登录", systemImage: "person.crop.circle")
                .font(.caption2).foregroundStyle(.secondary)
            if open {
                if status.email == nil, let email = model.previousSystemEmail {
                    Text("上次识别：\(email)").font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                        .truncationMode(.middle).help(email)
                }
                details(status, waiting: model.busy, stale: model.systemQuotaIsStale, signedIn: status.email != nil)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .quotaCardStyle(highlighted: active)
        .accessibilityIdentifier("antigravity-system-account")
    }

    private func savedCard(_ account: AntigravityAccount, active: Bool) -> some View {
        let status = model.saved[account.id] ?? AntigravityAccountStatus()
        let signingIn = model.signingIn.contains(account.id)
        let hasToken = account.hasToken
        let open = active || !collapsedAccounts.contains(account.id)
        return VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                accountTitle(account.name, status: status, unavailable: account.annotation?.unavailable == true)
                Spacer(minLength: 4)
                if !open {
                    if let fraction = AntigravityQuota.tightestGeminiBucket(in: status.groups)?.fraction {
                        Text(L.remaining(Fmt.percent(fraction * 100)))
                            .font(.caption.weight(.semibold)).monospacedDigit()
                            .foregroundStyle(quotaColor(remaining: fraction * 100))
                    } else if !hasToken && !signingIn {
                        Text("待登录").font(.caption2).foregroundStyle(.secondary)
                    }
                }
                AntigravityAccountMenu(account: account, hasToken: hasToken, model: model)
                if active || (hasToken && !signingIn) {
                    accountSwitch(active: active) { model.selectActiveAccount(account) }
                }
                if !active {
                    detailsToggle(open: open) {
                        if open { collapsedAccounts.insert(account.id) }
                        else { collapsedAccounts.remove(account.id) }
                    }
                }
            }
            if open {
                if let email = status.email, email.caseInsensitiveCompare(account.name) != .orderedSame {
                    Text(email).font(.caption2).foregroundStyle(.secondary)
                        .lineLimit(1).truncationMode(.middle).help(email)
                }
                if signingIn {
                    statusNotice(model.submittingCode ? "正在核验授权…" : "请在 Antigravity 登录窗口提交授权码。",
                                 icon: "person.badge.key")
                } else {
                    details(status, waiting: hasToken && model.busy, signedIn: hasToken)
                }
                HStack(spacing: 8) {
                    Button(signingIn ? "打开登录窗口" : (hasToken ? "重新登录" : "登录")) {
                        if signingIn { model.showSignInWindow() }
                        else { Task { await model.login(account) } }
                    }
                    .buttonStyle(.bordered).controlSize(.mini)
                    .disabled(!signingIn && (model.busy || !model.signingIn.isEmpty || DemoData.enabled))
                    if hasToken && !signingIn {
                        Button { Task { await model.refreshSavedAccount(account) } } label: {
                            Image(systemName: "arrow.clockwise").font(.caption)
                        }
                        .buttonStyle(.plain).disabled(model.busy).help("刷新此账号额度")
                        .accessibilityLabel("刷新此账号额度")
                    }
                }
                EditableAccountTags(annotation: account.annotation ?? AccountAnnotation()) {
                    AntigravityAccountMenu.editTags(account, model: model)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .quotaCardStyle(highlighted: active)
    }

    private func accountSwitch(active: Bool, action: @escaping () -> Void) -> some View {
        Group {
            if active {
                Text(L.inUse).font(.caption2.weight(.medium)).foregroundStyle(Color.accentColor)
                    .help("此账号显示在面板主卡和 Gemini 收起栏")
            } else {
                Button(L.switchBtn, action: action).buttonStyle(.bordered).controlSize(.mini)
                    .help("切换面板主卡和 Gemini 收起栏显示的账号")
            }
        }.fixedSize()
    }

    private func detailsToggle(open: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: open ? "chevron.up" : "chevron.right")
                .font(.caption2.weight(.semibold)).foregroundStyle(.secondary)
                .padding(.vertical, 3)
        }
        .buttonStyle(.plain).help(open ? L.collapseCard : L.expandCard)
        .accessibilityLabel(open ? L.collapseCard : L.expandCard)
    }

    private func systemSwitchNotice(_ pending: AntigravityModel.SystemSwitch) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("等待官方登录：\(pending.email)").font(.caption.weight(.medium))
                .lineLimit(1).truncationMode(.middle).help(pending.email)
            if pending.needsRetry {
                Text("尚未核对到目标账号，请确认官方客户端登录后重试。")
                    .font(.caption2).foregroundStyle(.orange)
            }
            HStack(spacing: 8) {
                Button("核对登录") { Task { await model.refreshSystemAccount() } }
                    .buttonStyle(.bordered).controlSize(.mini).disabled(model.busy)
                Button(L.cancel) { model.cancelSystemSwitch() }.buttonStyle(.link).font(.caption)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .quotaCardStyle(highlighted: false)
    }

    private func accountTitle(_ title: String, status: AntigravityAccountStatus,
                              stale: Bool = false, unavailable: Bool = false) -> some View {
        HStack(spacing: 6) {
            Circle().fill(statusColor(status, stale: stale, unavailable: unavailable)).frame(width: 8, height: 8)
                .accessibilityHidden(true)
            Text(title).font(.system(size: 13, weight: .semibold))
                .lineLimit(1).truncationMode(.middle).help(title)
            if let plan = status.planTier { PlanBadge(text: plan, small: true).lineLimit(1) }
        }
    }

    private func statusColor(_ status: AntigravityAccountStatus, stale: Bool, unavailable: Bool) -> Color {
        if unavailable { return .red }
        if stale { return .yellow }
        if status.error != nil || status.eligibility != nil { return .orange }
        if let fraction = AntigravityQuota.tightestGeminiBucket(in: status.groups)?.fraction {
            return quotaColor(remaining: fraction * 100)
        }
        return .gray
    }

    @ViewBuilder
    private func details(_ status: AntigravityAccountStatus, waiting: Bool, stale: Bool = false, signedIn: Bool) -> some View {
        if let eligibility = status.eligibility {
            AntigravityVerificationNotice(eligibility: eligibility)
        } else if let error = status.error {
            statusNotice(error, icon: "exclamationmark.triangle.fill", color: .orange)
        } else if status.groups.isEmpty {
            if waiting {
                HStack(spacing: 6) {
                    ProgressView().controlSize(.mini)
                    Text("正在读取额度…").font(.caption).foregroundStyle(.secondary)
                }
            } else {
                statusNotice(signedIn ? "等待额度同步" : "尚未登录", icon: signedIn ? "clock" : "person.crop.circle")
            }
        }
        ForEach(Array(status.groups.enumerated()), id: \.offset) { _, group in
            VStack(alignment: .leading, spacing: 6) {
                Text(group.name).font(.caption.weight(.semibold)).help(group.description ?? group.name)
                ForEach(Array(group.buckets.enumerated()), id: \.offset) { _, bucket in
                    QuotaWindowRow(label: bucket.title,
                                   remaining: bucket.disabled == true ? nil : bucket.fraction.map { $0 * 100 },
                                   reset: bucket.reportedResetDate, disabled: bucket.disabled == true, showsUsed: true)
                }
            }
        }
        if let sync = status.sync {
            Divider()
            HStack(alignment: .top, spacing: 8) {
                infoCell("AI Credits 余额", status.remainingCredits?.formatted() ?? L.dash)
                    .help("官方 Antigravity /credits 返回的剩余点数")
                infoCell(stale ? "上次同步（缓存）" : "额度更新时间", Fmt.fullDateTime.string(from: sync))
            }
            Text("来源 · Antigravity CLI").font(.system(size: 9)).foregroundStyle(.secondary)
        }
    }

    private func infoCell(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(.caption2).foregroundStyle(.secondary)
            Text(value).font(.caption).monospacedDigit().lineLimit(1).minimumScaleFactor(0.8)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func statusNotice(_ title: String, icon: String, color: Color = .secondary) -> some View {
        Label(title, systemImage: icon)
            .font(.caption2).foregroundStyle(color)
            .fixedSize(horizontal: false, vertical: true)
    }
}
