import AppKit
import SwiftUI

private struct DraftTag: Identifiable {
    let id = UUID()
    var text: String
}

struct AccountTagRow: View {
    let entry: QuotaMonitor.Entry
    @ObservedObject var monitor: QuotaMonitor

    var body: some View {
        EditableAccountTags(annotation: monitor.annotation(for: entry)) {
            TagEditorController.shared.open(entry: entry, monitor: monitor)
        }
    }
}

struct EditableAccountTags: View {
    let annotation: AccountAnnotation
    let edit: () -> Void

    var body: some View {
        TagFlowLayout(spacing: 4) {
            ForEach(Array(annotation.tags.enumerated()), id: \.offset) { item in
                let tag = item.element
                Button(action: edit) {
                    Text(tag)
                        .font(.system(size: 10))
                        .foregroundStyle(.red)
                        .padding(.horizontal, 6).padding(.vertical, 3)
                        .background(Color.red.opacity(0.10), in: RoundedRectangle(cornerRadius: 5))
                }
                .buttonStyle(.plain)
                .help(L.editTags + " · " + tag)
            }
            if annotation.unavailable {
                Text(L.markedUnavailable).font(.system(size: 9)).foregroundStyle(.red)
            }
            Button(action: edit) {
                Label(annotation.tags.isEmpty ? L.addTag : L.editTags, systemImage: annotation.tags.isEmpty ? "plus" : "pencil")
                    .font(.system(size: 10))
            }
            .buttonStyle(.link)
            .help(L.editTags)
        }
    }
}

private struct TagFlowLayout: Layout {
    let spacing: CGFloat

    private func positions(width: CGFloat, subviews: Subviews) -> (points: [CGPoint], sizes: [CGSize], height: CGFloat) {
        var points: [CGPoint] = [], sizes: [CGSize] = []
        var x: CGFloat = 0, y: CGFloat = 0, rowHeight: CGFloat = 0
        for subview in subviews {
            let size = subview.sizeThatFits(ProposedViewSize(width: width, height: nil))
            if x > 0 && x + size.width > width { x = 0; y += rowHeight + spacing; rowHeight = 0 }
            points.append(CGPoint(x: x, y: y)); sizes.append(size)
            x += size.width + spacing; rowHeight = max(rowHeight, size.height)
        }
        return (points, sizes, y + rowHeight)
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let idealWidth = subviews.reduce(CGFloat(0)) { $0 + $1.sizeThatFits(.unspecified).width }
            + spacing * CGFloat(max(0, subviews.count - 1))
        let width = proposal.width ?? idealWidth
        return CGSize(width: width, height: positions(width: width, subviews: subviews).height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let result = positions(width: bounds.width, subviews: subviews)
        for index in subviews.indices {
            subviews[index].place(at: CGPoint(x: bounds.minX + result.points[index].x, y: bounds.minY + result.points[index].y),
                                 proposal: ProposedViewSize(result.sizes[index]))
        }
    }
}

@MainActor
final class TagEditorController: NSObject, NSWindowDelegate {
    static let shared = TagEditorController()
    private var windows: [String: NSWindow] = [:]

    func open(entry: QuotaMonitor.Entry, monitor: QuotaMonitor) {
        let expectedKey = monitor.annotationKey(for: entry)
        open(id: "codex:" + entry.id, name: entry.profile.displayName,
             annotation: monitor.annotation(for: entry), hint: L.tagHint) { tags, unavailable in
            try monitor.saveAnnotations(entryId: entry.id, expectedKey: expectedKey, tags: tags, unavailable: unavailable)
        }
    }

    func open(id: String, name: String, annotation: AccountAnnotation, hint: String,
              save: @escaping ([String], Bool) throws -> Void) {
        if let window = windows[id] {
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 480, height: 460),
                              styleMask: [.titled, .closable], backing: .buffered, defer: false)
        let view = TagEditorView(name: name, annotation: annotation, hint: hint, save: save) { [weak window] in window?.close() }
        window.contentView = NSHostingView(rootView: view)
        window.title = L.editTags + " · " + name
        window.level = .floating
        window.isReleasedWhenClosed = false
        window.delegate = self
        windows[id] = window
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func windowWillClose(_ notification: Notification) {
        guard let window = notification.object as? NSWindow,
              let id = windows.first(where: { $0.value === window })?.key else { return }
        windows.removeValue(forKey: id)
    }

    func previewIfRequested(monitor: QuotaMonitor) {
        guard DemoData.enabled,
              let name = ProcessInfo.processInfo.environment["CODEX_MONITOR_TAG_EDITOR"],
              let entry = monitor.entries.first(where: { $0.profile.name == name }) else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
            self.open(entry: entry, monitor: monitor)
            guard let path = ProcessInfo.processInfo.environment["CODEX_MONITOR_TAG_EDITOR_SNAPSHOT"] else { return }
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
                guard let window = self.windows[entry.id], let view = window.contentView,
                      let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
                view.cacheDisplay(in: view.bounds, to: bitmap)
                if let data = bitmap.representation(using: .png, properties: [:]) {
                    try? data.write(to: URL(fileURLWithPath: path))
                    NSLog("[CodexMonitor] tag editor preview written; key window: %@", window.isKeyWindow ? "yes" : "no")
                }
            }
        }
    }
}

private struct TagEditorView: View {
    let name: String
    let hint: String
    let saveAnnotations: ([String], Bool) throws -> Void
    let close: () -> Void
    @State private var tags: [DraftTag]
    @State private var newTag = ""
    @State private var unavailable: Bool
    @State private var error = ""

    init(name: String, annotation: AccountAnnotation, hint: String,
         save: @escaping ([String], Bool) throws -> Void, close: @escaping () -> Void) {
        self.name = name; self.hint = hint; self.saveAnnotations = save; self.close = close
        _tags = State(initialValue: annotation.tags.map { DraftTag(text: $0) })
        _unavailable = State(initialValue: annotation.unavailable)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(name).font(.headline).textSelection(.enabled)
            Text(hint).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            HStack {
                TextField(L.tagPlaceholder, text: $newTag).onSubmit { addTag() }
                Button(L.addTag) { addTag() }.disabled(tags.count >= 8)
            }
            ScrollView {
                VStack(spacing: 8) {
                    ForEach($tags) { $tag in
                        HStack {
                            TextField(L.editTags, text: $tag.text)
                                .foregroundStyle(.red)
                                .accessibilityLabel(L.editTags)
                            Button(role: .destructive) { tags.removeAll { $0.id == tag.id } } label: {
                                Image(systemName: "trash")
                            }
                            .help(L.deleteTag).accessibilityLabel(L.deleteTag)
                        }
                    }
                }
            }
            .frame(maxHeight: .infinity)
            Text(L.tagLimits).font(.caption2).foregroundStyle(.secondary)
            Toggle(L.markUnavailable, isOn: $unavailable).help(L.tagUnavailableHelp)
            if !error.isEmpty { Text(error).foregroundStyle(.red).font(.caption) }
            HStack {
                Spacer()
                Button(L.cancel, action: close).keyboardShortcut(.cancelAction)
                Button(L.saveTags) { save() }.keyboardShortcut("s", modifiers: .command).buttonStyle(.borderedProminent)
            }
        }
        .textFieldStyle(.roundedBorder)
        .padding(22)
        .frame(width: 480, height: 460)
        .background(Color(nsColor: .windowBackgroundColor))
    }

    private func addTag() {
        let text = newTag.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        do {
            _ = try AccountAnnotations.validated(tags: tags.map(\.text) + [text], unavailable: unavailable)
            if !tags.contains(where: { $0.text == text }) { tags.append(DraftTag(text: text)) }
            newTag = ""; error = ""
        } catch { self.error = error.localizedDescription }
    }

    private func save() {
        var texts = tags.map(\.text)
        let pending = newTag.trimmingCharacters(in: .whitespacesAndNewlines)
        if !pending.isEmpty { texts.append(pending) }
        do {
            try saveAnnotations(texts, unavailable)
            close()
        } catch { self.error = error.localizedDescription }
    }
}
