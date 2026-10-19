import AppKit
import Carbon
import SwiftUI

final class AppDelegate: NSObject, NSApplicationDelegate {
    var panel: NSPanel?
    var menuBar: MenuBar?

    var window: NSWindow?

    func applicationDidFinishLaunching(_ notification: Notification) {
        if let path = Args.snapshot {
            if Args.demo != nil { snapshotDesk(to: path); return }
            snapshot(to: path)
            exit(0)
        }
        installEditMenu()
        if Args.menubar || Args.demo != nil {
            let bar = MenuBar(demo: Args.demo != nil)
            bar.start()
            menuBar = bar
            if let section = Args.demo {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) {
                    bar.showDesk(section == "sent" ? .sent : (section == "compose" ? .compose : .inbox))
                }
            }
            return
        }
        let content: NSViewController
        if let result = Args.result {
            let paired = result == "done"
            content = NSHostingController(rootView: ResultView(phone: Args.phone, paired: paired) { exit(0) })
            DispatchQueue.main.asyncAfter(deadline: .now() + (paired ? 2.5 : 30)) { exit(0) }
        } else {
            content = NSHostingController(rootView: PairView(phone: Args.phone) { code in
                if let code { print(code) }
                fflush(stdout)
                exit(0)
            })
        }
        let window = NSWindow(contentViewController: content)
        window.title = "Letterbox Desk"
        window.styleMask = [.titled, .closable, .fullSizeContentView]
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.appearance = nil
        window.backgroundColor = NSColor(Palette.ground)
        window.level = .floating
        window.center()
        window.makeKeyAndOrderFront(nil)
        self.window = window
        NSApp.activate(ignoringOtherApps: true)
        DispatchQueue.main.asyncAfter(deadline: .now() + 110) { exit(0) }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { !Args.menubar && Args.demo == nil }

    @MainActor private func snapshotDesk(to path: String) {
        let model = DeskModel(); model.loadFixture()
        let host = NSHostingView(rootView: DeskView(model: model))
        let frame = NSRect(x: 0, y: 0, width: 960, height: 760)
        host.frame = frame
        let window = NSWindow(contentRect: frame, styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.contentView = host
        window.appearance = NSAppearance(named: model.look == "day" ? .aqua : .darkAqua)
        window.center(); window.makeKeyAndOrderFront(nil); self.window = window
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
            host.layoutSubtreeIfNeeded()
            if let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds) {
                host.cacheDisplay(in: host.bounds, to: rep)
                try? rep.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: path))
            }
            window.close(); exit(0)
        }
    }

    @MainActor private func snapshot(to path: String) {
        let renderer: ImageRenderer<AnyView>
        if Args.demo != nil {
            let model = DeskModel(); model.loadFixture()
            renderer = ImageRenderer(content: AnyView(DeskView(model: model).frame(width: 960, height: 760)))
        } else if let result = Args.result {
            renderer = ImageRenderer(content: AnyView(ResultView(phone: Args.phone, paired: result == "done")))
        } else {
            renderer = ImageRenderer(content: AnyView(PairView(phone: Args.phone)))
        }
        renderer.scale = 2
        guard let image = renderer.cgImage else { return }
        let rep = NSBitmapImageRep(cgImage: image)
        try? rep.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: path))
    }
}

@main
enum PairWindowMain {
    static func main() {
        if CommandLine.arguments.contains("--letter") || CommandLine.arguments.contains("--sent") {
            FileHandle.standardError.write(Data("Letter cards have moved into Letterbox Desk.\n".utf8)); exit(2)
        }
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.setActivationPolicy(.accessory)
        app.run()
    }
}
