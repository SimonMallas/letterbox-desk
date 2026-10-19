import AppKit
import Carbon
import SwiftUI

final class MenuBar: NSObject, NSMenuDelegate, NSWindowDelegate {
    private var item: NSStatusItem?
    private let menu = NSMenu()
    private let model = DeskModel()
    private var phones = 0
    private var phoneEnabled = false
    private var phoneNotice: String?
    private var team: Team?
    private var window: NSWindow?
    private var result: NSPanel?
    private var hotKey: EventHotKeyRef?
    private var hintPanel: NSPanel?
    private let demo: Bool

    init(demo: Bool = false) {
        self.demo = demo
        super.init()
        if demo { model.loadFixture() }
        model.write = { [weak self] message in self?.write(message) }
    }

    func start() {
        if !demo {
            let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
            item.button?.image = NSImage(systemSymbolName: "bell", accessibilityDescription: "Letterbox Desk")
            menu.delegate = self
            item.menu = menu
            self.item = item
            rebuildMenu()
            registerHotKey()
            if Args.hint {
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { [weak self] in self?.showHint() }
            }
        }
        Thread.detachNewThread { [weak self] in
            while let line = readLine() {
                DispatchQueue.main.async { self?.handle(line) }
            }
            DispatchQueue.main.async { exit(0) }
        }
    }

    private func write(_ message: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: message),
              let line = String(data: data, encoding: .utf8) else { return }
        print(line)
        fflush(stdout)
    }

    func menuWillOpen(_ menu: NSMenu) {
        write(["team": true])
    }

    private func plain(_ title: String, mono: Bool = false, tip: String? = nil) -> NSMenuItem {
        let entry = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        entry.isEnabled = false
        if mono {
            entry.attributedTitle = NSAttributedString(string: title, attributes: [
                .font: NSFont.monospacedSystemFont(ofSize: 12, weight: .regular),
                .foregroundColor: NSColor.secondaryLabelColor])
        }
        entry.toolTip = tip
        return entry
    }

    private func teamLine(_ seat: TeamSeat) -> String {
        let name = seat.seat.padding(toLength: max(11, seat.seat.count + 1), withPad: " ", startingAt: 0)
        let waiting = seat.unacked.map { "\($0) awaiting ack" } ?? "? awaiting ack"
        let accepted = seat.accepted.map { "\($0) accepted" } ?? "? accepted"
        let status = !seat.reported.isEmpty ? "reports \u{201C}\(seat.reported)\u{201D} today"
            : (seat.has_status ? "status not current" : "status unknown")
        return "\(name)\(waiting) \u{00B7} \(accepted) \u{00B7} \(status)"
    }

    private func rebuildMenu() {
        guard item != nil else { return }
        menu.removeAllItems()
        let unread = model.unread
        let open = NSMenuItem(title: unread > 0 ? "Open Letterbox Desk \u{2014} \(unread) unread" : "Open Letterbox Desk",
                              action: #selector(openInbox), keyEquivalent: "")
        open.target = self
        open.isEnabled = !model.seats.isEmpty
        menu.addItem(open)
        let write = NSMenuItem(title: "New letter\u{2026}", action: #selector(openCompose), keyEquivalent: "b")
        write.keyEquivalentModifierMask = [.command, .option]
        write.target = self
        write.isEnabled = !model.seats.isEmpty
        menu.addItem(write)
        menu.addItem(.separator())
        let phone = NSMenuItem(title: "iPhone (Agent Bell)", action: #selector(togglePhone), keyEquivalent: "")
        phone.target = self
        phone.state = phoneEnabled ? .on : .off
        menu.addItem(phone)
        if let phoneNotice { menu.addItem(plain(phoneNotice)) }
        menu.addItem(.separator())
        let tip = "Read-only counts from the Letterbox\u{2019}s own team check. They don\u{2019}t show whether an agent is at its desk: a session that is offline still receives mail."
        menu.addItem(plain("Your team, from the Letterbox (not live presence)", tip: tip))
        if let team, team.checked > 0 {
            for seat in team.seats { menu.addItem(plain(teamLine(seat), mono: true, tip: tip)) }
            let checked = Words.when(Date(timeIntervalSince1970: TimeInterval(team.checked)))
            menu.addItem(plain(team.ok ? "Checked \(checked)" : "Last checked \(checked) \u{00B7} the latest check failed", tip: tip))
        } else {
            menu.addItem(plain(team == nil ? "Checking\u{2026}" : "Team status unavailable", tip: tip))
        }
        menu.addItem(.separator())
        menu.addItem(plain(model.seats.isEmpty ? "Listener starting\u{2026}"
                           : (phoneEnabled ? "Listener running \u{00B7} \(phones) phone\(phones == 1 ? "" : "s") paired"
                                           : "Listener running \u{00B7} Phone: off")))
        item?.button?.image = NSImage(systemSymbolName: unread > 0 ? "bell.badge" : "bell",
                                      accessibilityDescription: unread > 0 ? "Letterbox Desk, \(unread) unread" : "Letterbox Desk")
    }

    private func handle(_ line: String) {
        guard let data = line.data(using: .utf8),
              let message = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        let decoder = JSONDecoder()
        switch message["type"] as? String {
        case "conversations":
            if let snapshot = try? decoder.decode(ConversationSnapshot.self, from: data) {
                model.receive(snapshot)
                window?.appearance = model.look == "day" ? NSAppearance(named: .aqua) : model.look == "night" ? NSAppearance(named: .darkAqua) : nil
            }
        case "state":
            model.seats = message["seats"] as? [String] ?? []
            phones = message["phones"] as? Int ?? 0
            phoneEnabled = message["phone"] as? Bool ?? false
            if let notice = message["phone_notice"] as? String { phoneNotice = notice }
        case "mail":
            if let mail = try? decoder.decode(MailMessage.self, from: data) {
                if mail.inbox != model.inbox { model.inbox = mail.inbox }
                if mail.sent != model.sent { model.sent = mail.sent }
            }
        case "letter":
            if let letter = try? decoder.decode(LetterMessage.self, from: data) { model.receive(letter) }
        case "result":
            if let result = try? decoder.decode(ResultMessage.self, from: data) {
                model.receive(result)

            }
        case "team":
            team = try? decoder.decode(Team.self, from: data)
        default:
            return
        }
        rebuildMenu()
    }

    private func showHint() {
        guard let button = item?.button, let buttonWindow = button.window else { return }
        let host = NSHostingController(rootView: HintView { [weak self] in self?.hintPanel?.close() })
        let size = host.view.fittingSize
        let panel = NSPanel(contentRect: NSRect(origin: .zero, size: size),
                            styleMask: [.nonactivatingPanel, .borderless], backing: .buffered, defer: false)
        panel.contentViewController = host
        panel.backgroundColor = .clear
        panel.isOpaque = false
        panel.level = .statusBar
        panel.isReleasedWhenClosed = false
        let icon = buttonWindow.convertToScreen(button.convert(button.bounds, to: nil))
        var x = icon.midX - size.width / 2
        if let screen = buttonWindow.screen?.visibleFrame { x = min(max(x, screen.minX + 8), screen.maxX - size.width - 8) }
        panel.setFrameOrigin(NSPoint(x: x, y: icon.minY - size.height - 6))
        panel.orderFrontRegardless()
        hintPanel = panel
        DispatchQueue.main.asyncAfter(deadline: .now() + 12) { [weak self] in self?.hintPanel?.close() }
    }

    func windowWillClose(_ notification: Notification) {
        model.awaitingDeskSelection = false
        write(["conversation":["action":"close"]])
    }

    @objc func openInbox() { showDesk(.inbox) }

    @objc func openCompose() { model.chooseAgent(nil); showDesk(.compose) }

    @objc func togglePhone() { write(["phone_toggle": true]) }

    func showDesk(_ section: DeskSection) {
        guard demo || !model.seats.isEmpty else { return }
        if section == .inbox {model.openDesk()} else {model.section = section}
        if window == nil {
            let host = NSHostingController(rootView: DeskView(model: model))
            host.sizingOptions = []
            let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 960, height: 620),
                                  styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                                  backing: .buffered, defer: false)
            window.contentViewController = host
            window.delegate = self
            window.setContentSize(NSSize(width: 960, height: 620))
            window.contentMinSize = NSSize(width: 780, height: 500)
            window.title = "Letterbox Desk"
            window.titlebarAppearsTransparent = true
            window.titleVisibility = .hidden
            window.appearance = model.look == "day" ? NSAppearance(named: .aqua) : model.look == "night" ? NSAppearance(named: .darkAqua) : nil
            window.backgroundColor = NSColor(Palette.ground)
            window.isReleasedWhenClosed = false
            if !window.setFrameUsingName("LetterboxDesk") { window.center() }
            window.setFrameAutosaveName("LetterboxDesk")
            self.window = window
        }
        write(["refresh": true])
        write(["team": true])
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        if demo { FileHandle.standardError.write("window \(window?.windowNumber ?? 0)\n".data(using: .utf8)!) }
    }

    private func registerHotKey() {
        var eventType = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, _, userData in
            guard let userData else { return noErr }
            let me = Unmanaged<MenuBar>.fromOpaque(userData).takeUnretainedValue()
            DispatchQueue.main.async { me.openCompose() }
            return noErr
        }, 1, &eventType, Unmanaged.passUnretained(self).toOpaque(), nil)
        let id = EventHotKeyID(signature: OSType(0x42454C4C), id: 1)
        RegisterEventHotKey(UInt32(kVK_ANSI_B), UInt32(optionKey | cmdKey), id, GetApplicationEventTarget(), 0, &hotKey)
    }
}

/// Text fields only cut, copy, paste and undo through menu key equivalents, so even a menu-bar app needs an Edit menu.
func installEditMenu() {
    let main = NSMenu()
    let appItem = NSMenuItem()
    let appMenu = NSMenu()
    appMenu.addItem(withTitle: "Close Window", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
    appItem.submenu = appMenu
    main.addItem(appItem)
    let editItem = NSMenuItem()
    let edit = NSMenu(title: "Edit")
    edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
    edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "z").keyEquivalentModifierMask = [.command, .shift]
    edit.addItem(.separator())
    edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
    edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
    edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
    edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
    editItem.submenu = edit
    main.addItem(editItem)
    NSApp.mainMenu = main
}
