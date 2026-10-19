import AppKit
import Carbon
import Darwin
import SwiftUI

final class EditorTarget: ObservableObject {
    weak var textView: NSTextView?
    @Published var hint = ""
    @Published var dictating = false
    private var menuTimer: Timer?
    var mainMenu: () -> NSMenu? = {NSApp.mainMenu}
    static func dictationItem(in menu: NSMenu) -> (NSMenu, Int)? {
        for (index, item) in menu.items.enumerated() {
            if let submenu = item.submenu, let found = dictationItem(in: submenu) {return found}
            if let action = item.action, ["startDictation:", "stopDictation:"].contains(NSStringFromSelector(action)) {
                return (menu, index)
            }
        }
        return nil
    }
    func refreshDictation() {
        guard let menu = mainMenu(), let (owner, index) = Self.dictationItem(in: menu) else {dictating = false; return}
        owner.update()
        guard let item = owner.item(at: index) else {dictating = false; return}
        dictating = item.action == Selector(("stopDictation:")) || item.title.localizedCaseInsensitiveContains("stop dictation")
    }
    func watchDictation() {
        guard menuTimer == nil else {return}
        refreshDictation()
        menuTimer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] timer in
            guard let self else {timer.invalidate(); return}
            self.refreshDictation()
        }
    }
    func stopWatchingDictation() {menuTimer?.invalidate(); menuTimer = nil}

    var sendDictation: () -> Bool = {
        NSApp.sendAction(Selector(("startDictation:")), to: nil, from: nil)
    }

    func dictate() {
        guard let view = textView, let window = view.window else {hint = "Use the Dictation shortcut"; return}
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
        DispatchQueue.main.async { [weak self, weak view, weak window] in
            guard let self, let view, let window, view.window === window else {return}
            window.makeKeyAndOrderFront(nil)
            guard window.makeFirstResponder(view) else {self.hint = "Use the Dictation shortcut"; return}
            if let menu = self.mainMenu() {
                menu.update()
                if let (owner, index) = Self.dictationItem(in: menu) {
                    owner.update()
                    guard owner.item(at: index)?.isEnabled == true else {self.hint = "Use the Dictation shortcut"; return}
                    owner.performActionForItem(at: index)
                    self.hint = ""
                    self.refreshDictation()
                    return
                }
            }
            // AppKit owns Dictation; the editor itself does not implement this action.
            _ = self.sendDictation()
            self.hint = "Use the Dictation shortcut"
        }
    }
}

enum DraftPicture {
    static let maxBytes = 10 * 1024 * 1024
    enum ReadError: Error {case invalidFile, tooLarge}
    static func read(_ url: URL) throws -> Data {
        var original = stat()
        guard url.isFileURL, lstat(url.path, &original) == 0,
              (original.st_mode & S_IFMT) == S_IFREG else {throw ReadError.invalidFile}
        let resolved = url.resolvingSymlinksInPath()
        let fd = open(resolved.path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        guard fd >= 0 else {throw ReadError.invalidFile}
        let file = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        defer {try? file.close()}
        var info = stat()
        guard fstat(fd, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG,
              info.st_dev == original.st_dev, info.st_ino == original.st_ino else {throw ReadError.invalidFile}
        guard info.st_size <= maxBytes else {throw ReadError.tooLarge}
        var data = Data()
        while let chunk = try file.read(upToCount: min(65536, maxBytes + 1 - data.count)), !chunk.isEmpty {
            data.append(chunk)
            guard data.count <= maxBytes else {throw ReadError.tooLarge}
        }
        guard !data.isEmpty else {throw ReadError.invalidFile}
        return data
    }
}

final class DraftTextView: NSTextView {
    var placeholder = "" {didSet {needsDisplay = true}}
    var showsPlaceholder: Bool {string.isEmpty && !hasMarkedText()}
    override var string: String {didSet {needsDisplay = true}}
    override func setMarkedText(_ string: Any, selectedRange: NSRange, replacementRange: NSRange) {
        super.setMarkedText(string, selectedRange: selectedRange, replacementRange: replacementRange)
        needsDisplay = true
    }
    override func unmarkText() {super.unmarkText(); needsDisplay = true}
    override func didChangeText() {super.didChangeText(); needsDisplay = true}
    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        guard showsPlaceholder else {return}
        let origin = textContainerOrigin
        let padding = textContainer?.lineFragmentPadding ?? 5
        (placeholder as NSString).draw(in: NSRect(x: origin.x + padding, y: origin.y,
            width: max(0, bounds.width - origin.x - padding), height: bounds.height),
            withAttributes: [.font: font ?? NSFont.systemFont(ofSize: 13), .foregroundColor: NSColor.placeholderTextColor])
    }
    var picture: ((Data?, URL?) -> Void)?
    func takePicture(_ board: NSPasteboard) -> Bool {
        guard isEditable, let picture else {return false}
        if let urls = board.readObjects(forClasses: [NSURL.self]) as? [URL],
           let url = urls.first, ["png", "jpg", "jpeg"].contains(url.pathExtension.lowercased()) {
            picture(nil, url); return true
        }
        if let data = board.data(forType: .png) ?? board.data(forType: NSPasteboard.PasteboardType("public.jpeg")) {
            picture(data, nil); return true
        }
        return false
    }
    override func paste(_ sender: Any?) {
        if !takePicture(.general) {super.paste(sender)}
    }
    private func hasPicture(_ board: NSPasteboard) -> Bool {
        if board.availableType(from: [.png, NSPasteboard.PasteboardType("public.jpeg")]) != nil {return true}
        return (board.readObjects(forClasses: [NSURL.self]) as? [URL])?.contains {
            ["png", "jpg", "jpeg"].contains($0.pathExtension.lowercased())
        } ?? false
    }
    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        if picture != nil && hasPicture(sender.draggingPasteboard) {return .copy}
        return super.draggingEntered(sender)
    }
    override func prepareForDragOperation(_ sender: NSDraggingInfo) -> Bool {
        (picture != nil && hasPicture(sender.draggingPasteboard)) || super.prepareForDragOperation(sender)
    }
    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        takePicture(sender.draggingPasteboard) || super.performDragOperation(sender)
    }
}

struct NativeDraftEditor: NSViewRepresentable {
    @Binding var text: String
    let target: EditorTarget
    var placeholder: String = ""
    var onPicture: ((Data?, URL?) -> Void)?
    func makeCoordinator() -> Coordinator {Coordinator(self)}
    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSScrollView()
        scroll.drawsBackground = false; scroll.hasVerticalScroller = true
        let view = DraftTextView(frame: NSRect(x: 0, y: 0, width: 200, height: 80))
        view.isRichText = false; view.drawsBackground = false
        view.font = .systemFont(ofSize: 13); view.textColor = .labelColor; view.insertionPointColor = .labelColor
        view.isVerticallyResizable = true; view.isHorizontallyResizable = false
        view.autoresizingMask = [.width]
        view.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        view.textContainer?.widthTracksTextView = true
        view.textContainer?.containerSize = NSSize(width: 200, height: CGFloat.greatestFiniteMagnitude)
        view.delegate = context.coordinator; view.string = text; view.picture = onPicture; view.placeholder = placeholder
        view.registerForDraggedTypes([.fileURL, .png, NSPasteboard.PasteboardType("public.jpeg")])
        scroll.documentView = view; target.textView = view
        return scroll
    }
    func updateNSView(_ scroll: NSScrollView, context: Context) {
        context.coordinator.parent = self
        guard let view = scroll.documentView as? DraftTextView else {return}
        Self.applyText(text, to: view)
        view.picture = onPicture; view.placeholder = placeholder; target.textView = view
        view.isEditable = context.environment.isEnabled
    }
    static func applyText(_ text: String, to view: NSTextView) {
        if !view.hasMarkedText() && view.string != text {view.string = text}
    }
    final class Coordinator: NSObject, NSTextViewDelegate {
        var parent: NativeDraftEditor
        init(_ parent: NativeDraftEditor) {self.parent = parent}
        func textDidChange(_ notification: Notification) {
            guard let view = notification.object as? NSTextView else {return}
            parent.text = view.string
        }
    }
}

struct Editor: View {
    @Binding var text: String
    let placeholder: String
    var minHeight: CGFloat = 90
    var onPicture: ((Data?, URL?) -> Void)? = nil
    @StateObject private var target = EditorTarget()
    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            NativeDraftEditor(text: $text, target: target, placeholder: placeholder, onPicture: onPicture)
                .padding(.trailing, 34).frame(minHeight: minHeight)
                .overlay(alignment: .bottomTrailing) {DictateButton(target: target).offset(x: 4, y: 4)}
            if !target.hint.isEmpty {Text(target.hint).font(.system(size: 11)).foregroundStyle(Palette.muted)}
        }.padding(8).background(RoundedRectangle(cornerRadius: 10).fill(Palette.raised))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(Palette.line, lineWidth: 1))
            .onAppear {target.watchDictation()}.onDisappear {target.stopWatchingDictation()}
    }
}

struct DictateButton: View {
    @ObservedObject var target: EditorTarget
    var body: some View {
        Button {target.dictate()} label: {
            Image(systemName: target.dictating ? "stop.fill" : "mic.fill").font(.system(size: 14)).foregroundStyle(target.dictating ? Palette.warm : Palette.calm)
                .frame(width: 30, height: 30).background(Circle().fill(Palette.calm.opacity(0.14)))
                .overlay(Circle().stroke(Palette.calm.opacity(0.5), lineWidth: 1)).contentShape(Circle())
        }.buttonStyle(.plain).help(target.dictating ? "Stop Dictation" : "Start Dictation").accessibilityLabel(target.dictating ? "Stop Dictation" : "Start Dictation")
    }
}

struct OutcomeLines: View {
    let summary: Summary

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(Array(Words.lines(summary).enumerated()), id: \.offset) { _, line in
                HStack(alignment: .top, spacing: 6) {
                    Text(line.1 ? "\u{2713}" : "!")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(line.1 ? Palette.ok : Palette.warm)
                    Text(line.0)
                        .font(.system(size: 12))
                        .foregroundStyle(Palette.ink)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }
}

struct PaneTitle: View {
    let title: String
    let subtitle: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.system(size: 20, weight: .bold)).foregroundStyle(Palette.ink)
            Text(subtitle)
                .font(.system(size: 12))
                .foregroundStyle(Palette.muted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, 18)
        .padding(.top, 38)
        .padding(.bottom, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
