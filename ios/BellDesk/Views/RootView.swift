import SwiftUI
import UIKit
import UIKit.UIGestureRecognizerSubclass

/// The single desk screen: header, character stage, ticker, actions.
struct RootView: View {
    @EnvironmentObject private var model: AppModel
    @State private var showSettings = false

    var body: some View {
        ZStack {
            GeometryReader { geo in
                // iPhone 6s/7/8 (667 pt) get a shorter stage so the picker and button still fit.
                let small = geo.size.height + geo.safeAreaInsets.top + geo.safeAreaInsets.bottom < 700
                VStack(spacing: 0) {
                    Header()
                        .onLongPressGesture(minimumDuration: 0.6) { showSettings = true }
                        .padding(.top, max(small ? 4 : 0, (small ? 12 : 46) - geo.safeAreaInsets.top))
                    // While editing, the keyboard needs the room the character usually has.
                    if model.face != .editing {
                        StageView(face: model.face, forYou: model.ringMail?.isForYou ?? false, small: small)
                    }
                    TickerView()
                        .layoutPriority(model.face == .editing ? 1 : 0)
                    Spacer(minLength: small ? 8 : 12)
                    ActionsView()
                        .padding(.horizontal, 24)
                        .padding(.bottom, max(8, (small ? 16 : 40) - geo.safeAreaInsets.bottom))
                }
                .environment(\.smallScreen, small)
            }
            if model.dimmed {
                // OLED care: dim with a black layer, never the system brightness. The touch that wakes it does nothing else.
                Color.black.opacity(0.65)
                    .ignoresSafeArea()
                    .contentShape(Rectangle())
                    .onTapGesture {}
                    .transition(.opacity)
            }
        }
        .animation(model.dimmed ? .easeInOut(duration: 1.2) : nil, value: model.dimmed)
        .background(Palette.screen.ignoresSafeArea())
        .background(TouchWatcher { model.noteActivity() })
        .sheet(isPresented: $showSettings) {
            SettingsView().environmentObject(model)
        }
    }
}

private struct Header: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        HStack {
            Text("LETTERBOX BELL")
                .font(.mono(12, .medium))
                .tracking(1.68)
                .foregroundColor(Palette.muted)
            Spacer(minLength: 12)
            pill
        }
        .padding(.horizontal, 22)
        .frame(height: 44)
        .contentShape(Rectangle())
    }

    private var unpaired: Bool { model.face == .pair || model.face == .welcome }
    private var linkUp: Bool { !unpaired && model.link == .up }

    private var pillText: String {
        if unpaired { return "Not paired" }
        switch model.link {
        case .up: return model.macName
        case .connecting: return "Connecting"
        case .down: return "Mac offline"
        }
    }

    private var pill: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(linkUp ? Palette.calm : Color.clear)
                .overlay(Circle().strokeBorder(linkUp ? Color.clear : Palette.ring, lineWidth: 1.5))
                .frame(width: 8, height: 8)
                .shadow(color: linkUp ? Palette.calm.opacity(0.7) : .clear, radius: 4)
            Text(pillText)
                .font(.mono(12))
                .foregroundColor(Palette.ink2)
                .lineLimit(1)
        }
        .padding(.horizontal, 12)
        .frame(height: 28)
        .background(Capsule().fill(Palette.raised))
        .overlay(Capsule().strokeBorder(Palette.line, lineWidth: 1))
    }
}

struct StageView: View {
    let face: AppModel.Face
    /// A letter for the person rings in the calm colour instead of amber.
    let forYou: Bool
    let small: Bool

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var compact: Bool { [.review, .failed, .pair, .sending].contains(face) }

    private var characterOpacity: Double {
        switch face {
        case .pair: return 0.38
        case .offline: return 0.4
        default: return 1
        }
    }

    private var pool: Color {
        switch face {
        case .ring: return forYou ? Palette.calm.opacity(0.36) : Palette.ring.opacity(0.36)
        case .listening, .finishing: return Palette.calm.opacity(0.28)
        case .offline, .pair: return Palette.calm.opacity(0.04)
        default: return Palette.calm.opacity(0.10)
        }
    }

    private var height: CGFloat {
        switch (compact, small) {
        case (false, false): return 312
        case (true, false): return 236
        case (false, true): return 210
        case (true, true): return 160
        }
    }

    var body: some View {
        ZStack {
            Ellipse()
                .fill(pool)
                .frame(width: 240, height: 34)
                .blur(radius: face == .ring || face == .listening ? 14 : 12)
                .frame(maxHeight: .infinity, alignment: .bottom)
                .padding(.bottom, small ? 12 : 22)
            if face == .listening && !reduceMotion {
                Ripples()
                    .scaleEffect(small ? 0.68 : 1)
            }
            CharacterView(face: face)
                .transaction { $0.animation = nil }
                .scaleEffect((compact ? 0.72 : 1) * (small ? 0.68 : 1))
                .opacity(characterOpacity)
        }
        .frame(maxWidth: .infinity)
        .frame(height: height)
        .animation(.easeInOut(duration: 0.35), value: face)
    }
}

private struct Ripples: View {
    var body: some View {
        TimelineView(.animation) { context in
            let t = context.date.timeIntervalSinceReferenceDate
            ZStack {
                ForEach(0..<2, id: \.self) { i in
                    let p = (t + Double(i) * 0.9).truncatingRemainder(dividingBy: 1.8) / 1.8
                    let e = 1 - (1 - p) * (1 - p)
                    Circle()
                        .stroke(Palette.calm.opacity(0.55), lineWidth: 1)
                        .frame(width: 260, height: 260)
                        .scaleEffect(0.8 + 0.52 * e)
                        .opacity(0.75 * (1 - e))
                }
            }
        }
        .allowsHitTesting(false)
    }
}

/// Notices every touch anywhere in the window without taking part in it,
/// so it never competes with the hold gesture, buttons or scrolling.
private struct TouchWatcher: UIViewRepresentable {
    let onTouch: () -> Void

    func makeUIView(context: Context) -> WatcherView {
        let view = WatcherView()
        view.onTouch = onTouch
        return view
    }

    func updateUIView(_ view: WatcherView, context: Context) {
        view.onTouch = onTouch
    }

    final class WatcherView: UIView {
        var onTouch: () -> Void = {}
        private let recognizer = AnyTouch()

        override func didMoveToWindow() {
            super.didMoveToWindow()
            recognizer.onTouch = { [weak self] in self?.onTouch() }
            recognizer.cancelsTouchesInView = false
            recognizer.delaysTouchesBegan = false
            recognizer.delaysTouchesEnded = false
            window?.addGestureRecognizer(recognizer)
        }
    }

    final class AnyTouch: UIGestureRecognizer {
        var onTouch: () -> Void = {}

        override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent) {
            onTouch()
            state = .failed
        }
    }
}
