import AppKit
import Carbon
import SwiftUI

struct ResendAsk: Identifiable {
    let item: SentLetter
    let seat: String
    let mayBeThere: Bool
    var id: String { "\(item.request_id)/\(seat)" }
}

struct SentPane: View {
    @ObservedObject var model: DeskModel
    @State private var ask: ResendAsk?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            PaneTitle(title: "Sent", subtitle: "Every letter you sent from this Mac or your phone, and what happened to each copy. Nothing is ever resent unless you ask.")
            if model.sent.isEmpty {
                Text("Nothing sent yet.")
                    .font(.system(size: 12))
                    .foregroundStyle(Palette.muted)
                    .padding(18)
                Spacer()
            } else {
                ScrollView {
                    LazyVStack(spacing: 10) {
                        ForEach(model.sent) { item in
                            SentCard(model: model, item: item) { seat, mayBeThere in
                                ask = ResendAsk(item: item, seat: seat, mayBeThere: mayBeThere)
                            }
                        }
                    }
                    .padding(.horizontal, 18)
                    .padding(.bottom, 18)
                }
            }
        }
        .alert(item: $ask) { ask in
            Alert(title: Text("Send again to \(ask.seat)?"),
                  message: Text(ask.mayBeThere
                                ? "The first copy may already be in \(ask.seat)\u{2019}s inbox, so \(ask.seat) may get this letter twice."
                                : "The first attempt never ran, so this will be the first copy \(ask.seat) gets."),
                  primaryButton: .default(Text("Send again")) { model.resend(ask.item, ask.seat) },
                  secondaryButton: .cancel())
        }
    }
}

struct SentCard: View {
    @ObservedObject var model: DeskModel
    let item: SentLetter
    let onResend: (String, Bool) -> Void
    @State private var expanded = false

    private var heading: String {
        let when = Words.when(item.at > 0 ? Date(timeIntervalSince1970: TimeInterval(item.at)) : nil)
        let via = item.via == "mac" ? "from this Mac" : (item.via == "phone" ? "from your phone" : "")
        let to = "to " + item.results.map(\.to).joined(separator: ", ")
        return [when, to, via, item.re.isEmpty ? "" : "reply"].filter { !$0.isEmpty }.joined(separator: " \u{00B7} ")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(heading)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(Palette.muted)
                Spacer()
                Text(Words.title(item.status))
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(item.status == "sent" ? Palette.ok : (item.status == "sending" ? Palette.muted : Palette.warm))
            }
            if let text = item.text {
                Text(verbatim: text)
                    .font(.system(size: 13))
                    .foregroundStyle(Palette.ink)
                    .textSelection(.enabled)
                    .lineLimit(expanded ? nil : 4)
                    .frame(maxWidth: .infinity, alignment: .leading)
                if text.count > 280 || text.filter({ $0 == "\n" }).count > 3 {
                    Button(expanded ? "Show less" : "Show all") { expanded.toggle() }
                        .buttonStyle(.plain)
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(Palette.calm)
                }
            } else {
                Text("(The text of letters sent before Letterbox Desk wasn\u{2019}t kept.)")
                    .font(.system(size: 12))
                    .foregroundStyle(Palette.muted)
            }
            VStack(alignment: .leading, spacing: 6) {
                ForEach(item.results, id: \.to) { result in
                    seatRow(result)
                }
            }
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 10).fill(Palette.raised))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(Palette.line, lineWidth: 1))
    }

    @ViewBuilder
    private func seatRow(_ result: SeatOutcome) -> some View {
        let line = Words.seat(result)
        let key = "\(item.request_id)/\(result.to)"
        let canResend = item.text != nil && result.retried != true
            && (result.status == "not_confirmed" || result.status == "not_attempted")
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .top, spacing: 6) {
                Text(line.1 ? "\u{2713}" : (result.status == "sending" ? "\u{2026}" : "!"))
                    .font(.system(size: 12, weight: .bold))
                    .foregroundStyle(line.1 ? Palette.ok : Palette.warm)
                Text(line.0)
                    .font(.system(size: 12))
                    .foregroundStyle(Palette.ink)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer()
                if canResend {
                    Button(model.resending.contains(key) ? "Sending\u{2026}" : "Send again\u{2026}") {
                        onResend(result.to, result.status == "not_confirmed")
                    }
                    .buttonStyle(.plain)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(Palette.calm)
                    .disabled(model.resending.contains(key))
                }
            }
            if let again = model.resendResults[key] {
                OutcomeLines(summary: again).padding(.leading, 18)
            }
        }
    }
}
