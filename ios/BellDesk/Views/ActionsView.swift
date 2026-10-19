import SwiftUI
import UIKit

/// Bottom of the screen: seat picker, hold-to-talk, and the buttons for each decision.
struct ActionsView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        let face = model.face
        let settledSent = face == .sent && model.unresolved.isEmpty
        VStack(spacing: 14) {
            if face == .welcome {
                PillButton(title: "Continue", prominent: true) { model.continueFromWelcome() }
            }
            if (face == .pair || face == .offline) && model.networkProblem == .localNetworkDenied {
                PillButton(title: "Open Settings", prominent: true) {
                    if let url = URL(string: UIApplication.openSettingsURLString) {
                        UIApplication.shared.open(url)
                    }
                }
            }
            if (face == .idle || face == .ring || settledSent) && !model.seats.isEmpty {
                SeatPicker()
            }
            if face == .idle || face == .ring || face == .listening || face == .finishing || face == .offline || settledSent {
                HoldButton()
            }
            if face == .review {
                HStack(spacing: 12) {
                    PillButton(title: "Say it again", prominent: false) { model.sayItAgain() }
                    PillButton(title: "Discard", prominent: false) { model.discard() }
                }
                PillButton(title: "Send", prominent: true) { model.send() }
                    .disabled(!model.canSend)
                    .opacity(model.canSend ? 1 : 0.4)
            }
            if face == .editing {
                PillButton(title: "Done", prominent: true) { model.finishEditing() }
            }
            if face == .failed || (face == .sent && !settledSent) {
                ResolveButtons()
            }
            if face == .pair && model.pairPhase == .failed && model.networkProblem == nil {
                PillButton(title: "Try again", prominent: true) { model.retryPairing() }
            }
            if face == .sending && model.sendPhase == .checking {
                PillButton(title: "Stop checking", prominent: false) { model.stopChecking() }
            }
        }
    }
}

/// After a send that didn't land everywhere. Seats that were never sent can be sent again
/// directly; seats the Mac couldn't confirm need a second, explicit yes because they may get it twice.
private struct ResolveButtons: View {
    @EnvironmentObject private var model: AppModel
    @State private var confirmTwice = false

    var body: some View {
        let unattempted = model.unresolved.filter { $0.state == .notAttempted }.map(\.seat)
        let unconfirmed = model.unresolved.filter { $0.state == .notConfirmed }.map(\.seat)
        let who = model.phrase(for: unconfirmed)
        VStack(spacing: 10) {
            if !unattempted.isEmpty {
                PillButton(title: "Send again to \(model.phrase(for: unattempted))", prominent: true) {
                    model.sendAgainToUnattempted()
                }
            }
            if !unconfirmed.isEmpty {
                PillButton(title: "Send again to \(who) (may arrive twice)", prominent: false) {
                    confirmTwice = true
                }
            }
            PillButton(title: model.face == .failed ? "Discard" : "Done", prominent: false) { model.discard() }
        }
        .confirmationDialog("Send again to \(who)?", isPresented: $confirmTwice, titleVisibility: .visible) {
            Button("Send again anyway") { model.sendAgainToUnconfirmed() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("The Mac couldn't confirm the first copy, and it may already have arrived. \(who) may get this letter twice.")
        }
    }
}

private struct SeatPicker: View {
    @EnvironmentObject private var model: AppModel

    private let columns = Array(repeating: GridItem(.flexible(), spacing: 8), count: 3)

    var body: some View {
        let chosen = Set(model.recipients)
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("WRITE TO")
                    .font(.mono(11, .medium))
                    .tracking(1.54)
                    .foregroundColor(Palette.muted)
                Spacer()
                Chip(title: "Everyone", selected: model.everyone, height: 26) { model.chooseEveryone() }
                    .fixedSize()
            }
            LazyVGrid(columns: columns, spacing: 8) {
                ForEach(Array(model.seats.enumerated()), id: \.offset) { _, seat in
                    Chip(title: seat, selected: chosen.contains(seat), height: 44) { model.toggle(seat) }
                }
            }
        }
    }
}

private struct Chip: View {
    let title: String
    let selected: Bool
    let height: CGFloat
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.mono(13))
                .lineLimit(1)
                .minimumScaleFactor(0.7)
                .foregroundColor(selected ? Palette.ink : Palette.ink2)
                .padding(.horizontal, height < 40 ? 12 : 6)
                .frame(maxWidth: height < 40 ? nil : .infinity)
                .frame(height: height)
                .background(RoundedRectangle(cornerRadius: 12).fill(Palette.raised))
                .overlay(RoundedRectangle(cornerRadius: 12)
                    .strokeBorder(selected ? Palette.calm : Palette.line, lineWidth: selected ? 2 : 1))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

/// Press and hold to dictate; release to review.
private struct HoldButton: View {
    @EnvironmentObject private var model: AppModel
    @GestureState private var pressing = false

    var body: some View {
        let listening = model.face == .listening
        let disabled = !listening && !model.canHold
        HStack(spacing: 10) {
            Image(systemName: "mic")
                .font(.system(size: 19))
            Text(label)
                .font(.ui(17, .medium))
                .lineLimit(1)
                .minimumScaleFactor(0.8)
        }
        .foregroundColor(listening ? Palette.screen : disabled ? Palette.faint : Palette.ink)
        .padding(.horizontal, 16)
        .frame(maxWidth: .infinity)
        .frame(height: 64)
        .background(Capsule().fill(listening ? Palette.calm : disabled ? Palette.screen : Palette.raised))
        .overlay(Capsule().strokeBorder(listening ? Palette.calm : Palette.raiseLine,
                                        style: StrokeStyle(lineWidth: 1, dash: disabled ? [4, 3] : [])))
        .contentShape(Capsule())
        .gesture(DragGesture(minimumDistance: 0).updating($pressing) { _, state, _ in state = true })
        .onChange(of: pressing) { down in
            if down { model.holdBegan() } else { model.holdEnded() }
        }
        .animation(.easeOut(duration: 0.2), value: listening)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(label)
        .accessibilityAddTraits(.isButton)
    }

    private var label: String {
        if model.face == .listening { return "Release to review" }
        if model.face == .finishing { return "Finishing…" }
        if model.face == .offline { return "Reconnect to write" }
        if model.link == .connecting { return "Connecting…" }
        if model.recipients.isEmpty { return "No seats from the Mac" }
        return "Hold to write to \(model.phrase(for: model.recipients))"
    }
}

private struct PillButton: View {
    let title: String
    let prominent: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.ui(17, prominent ? .semibold : .medium))
                .lineLimit(1)
                .minimumScaleFactor(0.75)
                .foregroundColor(prominent ? Palette.screen : Palette.ink)
                .padding(.horizontal, 16)
                .frame(maxWidth: .infinity)
                .frame(height: 56)
                .background(Capsule().fill(prominent ? Palette.calm : Palette.raised))
                .overlay(Capsule().strokeBorder(prominent ? Palette.calm : Palette.raiseLine, lineWidth: 1))
                .contentShape(Capsule())
        }
        .buttonStyle(.plain)
    }
}
