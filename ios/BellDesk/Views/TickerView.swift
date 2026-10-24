import SwiftUI

/// The words under the character, one block per face.
struct TickerView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.top, 4)
        .padding(.horizontal, 28)
    }

    @ViewBuilder private var content: some View {
        switch model.face {
        case .welcome: welcome
        case .pair: if let problem = model.networkProblem { networkProblem(problem) } else { pair }
        case .paired: paired
        case .idle: idle
        case .offline: if let problem = model.networkProblem { networkProblem(problem) } else { offline }
        case .ring: ring
        case .listening: listening
        case .finishing: finishing
        case .review: review
        case .editing: editing
        case .sending: sending
        case .sent: sent
        case .failed: failed
        }
    }

    // MARK: Faces

    @ViewBuilder private var welcome: some View {
        TLabel(text: "Getting started", color: Palette.calm)
        Text("Bell needs to find your Mac on this Wi-Fi.").tBig()
        Text("Tap Allow on the next message.").tNote()
    }

    @ViewBuilder private func networkProblem(_ problem: AppModel.NetworkProblem) -> some View {
        switch problem {
        case .noWiFi:
            TLabel(text: "No Wi-Fi", color: Palette.ring)
            Text("Connect this iPhone to the same Wi-Fi as your Mac.").tMid()
            Text("Bell finds your Mac, and hears it ring, over the local network.").tNote()
        case .localNetworkDenied:
            TLabel(text: "Local Network is off", color: Palette.ring)
            Text("Bell can't look for your Mac").tMid()
            Text("To fix it, open Settings → Privacy & Security → Local Network, and turn on Bell.").tNote()
        }
    }

    @ViewBuilder private var paired: some View {
        TLabel(text: model.macName, color: Palette.calm)
        Text("Paired ✓").tBig()
        Text("You're all set").tMid()
    }

    @ViewBuilder private var pair: some View {
        TLabel(text: "Not paired yet", color: Palette.ring)
        if model.pairPhase == .failed {
            Text("Pairing failed: try again").tBig()
            Text("Something didn't check out, so nothing was paired. Tap Try again.").tNote()
        } else if model.pairPhase == .wrongMacCode {
            Text("Nothing was paired").tBig()
            Text("That isn't the code your Mac shows. If your Mac shows no code, this phone may have reached a different Mac on this Wi-Fi.").tNote()
        } else if case .confirm(let retry) = model.pairPhase {
            Text("Now type your Mac's code").tBig()
            Text(retry ? "That isn't the code your Mac shows. Check it and try once more."
                       : "Your Mac now shows a code. Type it here to finish pairing.").tNote()
            MacCodeField(retry: retry)
        } else {
            Text("Pair with the Mac").tBig()
            if case .code(let code) = model.pairPhase {
                Text("Type this code into the pop-up on your Mac.").tNote()
                Text(BellCrypto.displayCode(code))
                    .font(.mono(42, .medium))
                    .tracking(5)
                    .monospacedDigit()
                    .foregroundColor(Palette.ink)
                    .padding(.vertical, 6)
            } else {
                Text("On your Mac, choose Pair a phone\u{2026} from the bell in the menu bar. A code will appear here.").tNote()
            }
            HStack(spacing: 8) {
                Pulse(size: 7, color: Palette.calm)
                Text(pairLine).tLine(Palette.muted)
            }
        }
        if let reason = model.pairReason {
            Text(reason).tFoot()
        }
    }

    private var pairLine: String {
        switch model.pairPhase {
        case .searching: return "Looking for your Mac on this Wi-Fi"
        case .unreachable(let host): return "Can't reach \(host)"
        case .windowClosed: return "Pairing isn't open on \(model.macName)"
        case .tooOld: return "Update Letterbox Desk on \(model.macName) to pair"
        case .code: return "Waiting for you to type it on the Mac"
        case .confirm, .confirming: return "Finishing pairing with \(model.macName)"
        case .denied: return "The Mac didn't accept that code"
        case .expired: return "The code expired"
        case .failed, .wrongMacCode: return ""
        }
    }

    @ViewBuilder private var idle: some View {
        Text("Watching the letterbox").tTitle()
        // The one-time tip borrows the idle line's room unless there is news to show.
        if model.tipDismissed || model.notice != nil || model.link == .connecting {
            Text(idleLine).tLine(model.notice == nil ? Palette.muted : Palette.calm)
        }
        if !model.tipDismissed {
            TipCard { model.dismissTip() }
        }
    }

    private var idleLine: String {
        if let notice = model.notice { return notice }
        if model.link == .connecting { return "Connecting to the listener" }
        guard let m = model.lastMail else { return "No urgent letters since your Mac started listening." }
        let time = Clock.hhmm(envelopeTS: m.ts).map { " · \($0)" } ?? ""
        return "Last: \(headline(m))\(time)"
    }

    /// "<from> wrote to you", "<to> got a new task from <from>".
    private func headline(_ m: MailEnvelope) -> String {
        m.isForYou ? "\(m.from) wrote to you" : "\(m.to) got \(LetterWords.phrase(m.letterType)) from \(m.from)"
    }

    @ViewBuilder private var offline: some View {
        switch model.mistrust {
        case .keyChanged:
            TLabel(text: "Not your Mac", color: Palette.ring)
            Text("This Mac doesn't match the one you paired with").tMid()
            Text("Bell won't send to it. If you reset Letterbox Desk or changed Mac, tap Pair again.").tNote()
        case .notRecognised:
            TLabel(text: "Not recognised", color: Palette.ring)
            Text("Your Mac no longer recognises this phone").tMid()
            Text("It may have been unpaired on the Mac. Bell won't send until you pair again.").tNote()
        case nil:
            TLabel(text: "Mac offline", color: Palette.ring)
            Text("Can't reach the listener").tMid()
            Text("Rings pause until the link is back. Letters are safe in the inbox meanwhile.").tNote()
        }
    }

    @ViewBuilder private var ring: some View {
        if let m = model.ringMail {
            if m.isForYou {
                TLabel(text: "For you", color: Palette.calm, accessory: .pulse)
            } else {
                TLabel(text: "New mail · priority now", color: Palette.ring, accessory: .pulse)
            }
            Text(headline(m)).tBig()
            Text("\(LetterWords.word(m.letterType)) · \(Clock.hhmm(envelopeTS: m.ts) ?? Clock.hhmm(model.ringAt))")
                .tLine(Palette.ink2)
            Text("Envelope \(m.shortID) · letter stays on the Mac").tFoot()
        }
    }

    @ViewBuilder private var listening: some View {
        TLabel(text: model.speechOnDevice ? "Listening · on this phone" : "Listening", color: Palette.calm, accessory: .eq)
        heard
    }

    /// Mic is off; the recogniser's final result is on its way (up to 1.5 s).
    @ViewBuilder private var finishing: some View {
        TLabel(text: "Finishing…", color: Palette.calm, accessory: .pulse)
        heard
    }

    @ViewBuilder private var heard: some View {
        let to = model.phrase(for: model.recipients)
        Group {
            if model.transcript.isEmpty {
                Text("Say something for \(to)…").foregroundColor(Palette.faint)
            } else {
                Text(model.transcript).foregroundColor(Palette.ink)
            }
        }
        .font(.ui(20))
        .lineSpacing(5)
        .lineLimit(5)
        .truncationMode(.head)
        .frame(minHeight: 58, alignment: .topLeading)
        Text(model.speechOnDevice
             ? "For \(to), from desk · speech stays on the phone until you send"
             : "For \(to), from desk · speech goes to Apple's online service; nothing reaches the Mac until you send")
            .tFoot()
    }

    @ViewBuilder private var review: some View {
        TLabel(text: "Review before sending", color: Palette.calm)
        LetterCard(head: "desk → \(model.reviewSeats.joined(separator: ", ")) · info", text: model.transcript,
                   onEdit: { model.beginEditing() })
        Text(reviewNote).tNote()
    }

    private var reviewNote: String {
        let length = model.letterLength
        if length == 0 { return "Nothing to send. Edit it, or say it again." }
        if length > AppModel.maxLetterLength {
            return "Too long: \(length) of \(AppModel.maxLetterLength) characters. Tap the words to shorten it."
        }
        let fix = "Tap the words to fix them. Nothing is sent until you tap Send."
        return model.reviewSeats.count > 1 ? "Each copy lists the other agents you addressed. \(fix)" : fix
    }

    @ViewBuilder private var editing: some View {
        let length = model.letterLength
        HStack {
            TLabel(text: "Edit before sending", color: Palette.calm)
            Spacer()
            Text("\(length) / \(AppModel.maxLetterLength)")
                .font(.mono(12))
                .foregroundColor(length > AppModel.maxLetterLength ? Palette.ring : Palette.faint)
        }
        LetterEditor(head: "desk → \(model.reviewSeats.joined(separator: ", ")) · info")
    }

    @ViewBuilder private var sending: some View {
        let checking = model.sendPhase == .checking
        TLabel(text: checking ? "Checking with the Mac" : "Sending", color: Palette.calm, accessory: .pulse)
        LetterCard(head: "desk → \(model.sendTo.joined(separator: ", ")) · info", text: model.transcript)
        Text(checking
             ? "No answer from the Mac. When it's reachable, the phone asks once whether the letter arrived. Nothing is re-sent."
             : "Waiting for the Mac to confirm.")
            .tNote()
    }

    @ViewBuilder private var sent: some View {
        let outcomes = model.outcomes
        let settled = model.unresolved.isEmpty
        TLabel(text: settled ? "Sent" : "Partly sent", color: settled ? Palette.calm : Palette.ring)
        if outcomes.count == 1, let only = outcomes.first {
            Text(seatLine(only)).tBig()
            Text("desk → \(only.seat) · info · \(String((only.envelopeID ?? "").suffix(8)))").tLine(Palette.ink2)
            Text(Clock.hhmm(model.sentAt)).tLine(Palette.muted)
            if let reason = only.reason, !reason.isEmpty {
                Text(reason).tFoot()
            }
        } else {
            SeatLines(outcomes: outcomes, line: seatLine)
            Text("desk → \(outcomes.map(\.seat).joined(separator: ", ")) · info · \(Clock.hhmm(model.sentAt))")
                .tLine(Palette.muted)
        }
    }

    /// PROTOCOL.md "What the phone shows", per seat. Never "read" or "delivered".
    private func seatLine(_ o: AppModel.SeatOutcome) -> String {
        switch o.state {
        case .published:
            switch o.ringOutcome {
            case "submitted": return "In \(o.seat)'s inbox · doorbell rang"
            case "no_live_surface": return "In \(o.seat)'s inbox · the doorbell didn't ring"
            case let other?: return "In \(o.seat)'s inbox · doorbell: \(other)"
            case nil: return "In \(o.seat)'s inbox"
            }
        case .notConfirmed:
            return "\(o.seat): the Mac couldn't confirm it (it may have arrived)"
        case .notAttempted:
            return "\(o.seat): not sent"
        }
    }

    @ViewBuilder private var failed: some View {
        if let f = model.failure {
            TLabel(text: f.label, color: Palette.ring)
            Text(f.title).tMid()
            Text(f.note).tNote()
            LetterCard(head: nil, text: model.transcript)
            if model.outcomes.count > 1 {
                SeatLines(outcomes: model.outcomes, line: seatLine)
            } else if let reason = model.outcomes.first?.reason {
                Text("reason: \(reason)").tFoot()
            }
        }
    }
}

// MARK: Pieces

/// One line per seat; anything not in an inbox is in the warning colour.
private struct SeatLines: View {
    let outcomes: [AppModel.SeatOutcome]
    let line: (AppModel.SeatOutcome) -> String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(outcomes, id: \.seat) { o in
                Text(line(o))
                    .font(.ui(16, .medium))
                    .foregroundColor(o.state == .published ? Palette.ink : Palette.ring)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

private struct TipCard: View {
    let dismiss: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Leave Bell open on your desk. It keeps the screen on and rings only while it's open.")
                .font(.ui(15))
                .lineSpacing(3)
                .foregroundColor(Palette.ink2)
                .fixedSize(horizontal: false, vertical: true)
            Button("Got it", action: dismiss)
                .font(.ui(15, .semibold))
                .foregroundColor(Palette.calm)
                .buttonStyle(.plain)
        }
        .padding(.vertical, 12)
        .padding(.horizontal, 16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 18).fill(Palette.raised))
        .overlay(RoundedRectangle(cornerRadius: 18).strokeBorder(Palette.line, lineWidth: 1))
    }
}

/// The raised card holding the dictated text. Hugs the text; scrolls only past a height cap.
private struct LetterCard: View {
    let head: String?
    let text: String
    /// When set, the card shows Edit and tapping the words opens the editor.
    var onEdit: (() -> Void)? = nil

    @State private var textHeight: CGFloat = 0
    @Environment(\.smallScreen) private var small

    private var cap: CGFloat {
        switch (head == nil, small) {
        case (true, false): return 140
        case (false, false): return 250
        case (true, true): return 90
        case (false, true): return 140
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let head {
                HStack {
                    Text(head).font(.mono(12)).foregroundColor(Palette.muted)
                    Spacer(minLength: 8)
                    if let onEdit {
                        Button("Edit", action: onEdit)
                            .font(.ui(15, .semibold))
                            .foregroundColor(Palette.calm)
                            .buttonStyle(.plain)
                    }
                }
            }
            ScrollView {
                letterText
                    .background(GeometryReader { geo in
                        Color.clear.preference(key: TextHeightKey.self, value: geo.size.height)
                    })
            }
            // Hugs short text; gives way (and scrolls) when the screen has less room than the cap.
            .frame(maxHeight: min(textHeight, cap))
            .noBounceWhenFits(textHeight <= cap)
            .onPreferenceChange(TextHeightKey.self) { textHeight = $0 }
            .onTapGesture { onEdit?() }
        }
        .padding(.vertical, head == nil ? 12 : 16)
        .padding(.horizontal, head == nil ? 16 : 18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 18).fill(Palette.raised))
        .overlay(RoundedRectangle(cornerRadius: 18).strokeBorder(Palette.line, lineWidth: 1))
    }

    private var letterText: some View {
        Text(text)
            .font(.ui(head == nil ? 15 : 18))
            .lineSpacing(head == nil ? 3 : 4.5)
            .foregroundColor(head == nil ? Palette.ink2 : Palette.ink)
            .frame(maxWidth: .infinity, alignment: .leading)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// Where the person types the code the Mac shows. It is checked here; the phone never displays it.
private struct MacCodeField: View {
    @EnvironmentObject private var model: AppModel
    let retry: Bool

    @State private var code = ""
    @FocusState private var focused: Bool

    var body: some View {
        TextField("000000", text: $code)
            .font(.mono(42, .medium))
            .monospacedDigit()
            .foregroundColor(Palette.ink)
            .tint(Palette.calm)
            .keyboardType(.numberPad)
            .focused($focused)
            .padding(.vertical, 6)
            .accessibilityLabel("Code shown on your Mac")
            .onChange(of: code) { typed in
                let digits = String(BellCrypto.typedDigits(typed).prefix(6))
                // Checked only once the field holds just these digits, so tidying it isn't a second try.
                if digits != typed { code = digits } else if digits.count == 6 { model.submitMacCode(digits) }
            }
            .onChange(of: retry) { _ in code = "" }
            .onAppear {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { focused = true }
            }
    }
}

/// The letter as editable text, keyboard up. Fills the space the hidden stage leaves.
private struct LetterEditor: View {
    @EnvironmentObject private var model: AppModel
    let head: String

    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(head).font(.mono(12)).foregroundColor(Palette.muted)
            TextEditor(text: Binding(get: { model.transcript }, set: { model.setDraft($0) }))
                .font(.ui(18))
                .foregroundColor(Palette.ink)
                .tint(Palette.calm)
                .clearEditorBackground()
                .focused($focused)
                .frame(maxHeight: .infinity)
                .accessibilityLabel("Letter text")
        }
        .padding(.vertical, 16)
        .padding(.horizontal, 14)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(RoundedRectangle(cornerRadius: 18).fill(Palette.raised))
        .overlay(RoundedRectangle(cornerRadius: 18).strokeBorder(Palette.calm, lineWidth: 1))
        .onAppear {
            // Opening the keyboard in the same pass as the view appears is unreliable.
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { focused = true }
        }
    }
}

private extension View {
    /// iOS 15 cannot hide TextEditor's own background; there it stays the system dark.
    @ViewBuilder func clearEditorBackground() -> some View {
        if #available(iOS 16.0, *) {
            scrollContentBackground(.hidden)
        } else {
            self
        }
    }

    /// iOS 15 has no scrollDisabled; there a short card just bounces.
    @ViewBuilder func noBounceWhenFits(_ fits: Bool) -> some View {
        if #available(iOS 16.0, *) {
            scrollDisabled(fits)
        } else {
            self
        }
    }
}

private struct TextHeightKey: PreferenceKey {
    static let defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = max(value, nextValue()) }
}

struct TLabel: View {
    enum Accessory { case none, pulse, eq }

    let text: String
    let color: Color
    var accessory: Accessory = .none

    var body: some View {
        HStack(spacing: 8) {
            switch accessory {
            case .none: EmptyView()
            case .pulse: Pulse(size: 8, color: color)
            case .eq: EqBars(color: color)
            }
            Text(text.uppercased())
                .font(.mono(12, .medium))
                .tracking(1.68)
        }
        .foregroundColor(color)
    }
}

struct Pulse: View {
    let size: CGFloat
    let color: Color

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 30, paused: reduceMotion)) { context in
            let t = context.date.timeIntervalSinceReferenceDate
            Circle()
                .fill(color)
                .frame(width: size, height: size)
                .opacity(reduceMotion ? 1 : 0.625 + 0.375 * cos(2 * .pi * t / 1.4))
        }
        .frame(width: size, height: size)
    }
}

private struct EqBars: View {
    let color: Color

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 30, paused: reduceMotion)) { context in
            let t = context.date.timeIntervalSinceReferenceDate
            HStack(alignment: .bottom, spacing: 3) {
                ForEach(0..<4, id: \.self) { i in
                    let wave = (1 - cos(2 * .pi * (t - Double(i) * 0.15) / 0.9)) / 2
                    RoundedRectangle(cornerRadius: 2)
                        .fill(color)
                        .frame(width: 3, height: 14)
                        .scaleEffect(x: 1, y: reduceMotion ? 1 : 0.3 + 0.7 * wave, anchor: .bottom)
                }
            }
        }
        .frame(height: 14)
    }
}

private extension Text {
    func tTitle() -> some View {
        font(.ui(22, .medium)).tracking(-0.22).foregroundColor(Palette.ink)
    }

    func tBig() -> some View {
        font(.ui(26, .semibold)).tracking(-0.39).foregroundColor(Palette.ink)
            .fixedSize(horizontal: false, vertical: true)
    }

    func tMid() -> some View {
        font(.ui(21, .semibold)).foregroundColor(Palette.ink)
            .fixedSize(horizontal: false, vertical: true)
    }

    func tLine(_ color: Color) -> some View {
        font(.mono(13)).foregroundColor(color).lineSpacing(3)
    }

    func tNote() -> some View {
        font(.ui(14)).lineSpacing(3).foregroundColor(Palette.muted)
            .fixedSize(horizontal: false, vertical: true)
    }

    func tFoot() -> some View {
        font(.mono(11)).lineSpacing(4).foregroundColor(Palette.faint)
            .fixedSize(horizontal: false, vertical: true)
    }
}
