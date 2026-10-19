import AppKit
import Carbon
import SwiftUI

struct PairView: View {
    let phone: String
    var onDone: (String?) -> Void = { _ in }
    @State private var code = Args.preset
    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            VStack(alignment: .leading, spacing: 6) {
                Text("Pair a phone")
                    .font(.system(size: 24, weight: .bold))
                    .foregroundStyle(Palette.ink)
                Text("Connect Agent Bell on your iPhone to Letterbox Desk.")
                    .font(.system(size: 13))
                    .foregroundStyle(Palette.muted)
            }
            HStack(spacing: 8) {
                Step(number: "1", label: "Open Bell", state: -1)
                Step(number: "2", label: "Enter code", state: 0)
                Step(number: "3", label: "Done", state: 1)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text("Enter the code")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(Palette.ink)
                Text("Type the 6 digits showing on \u{201C}\(phone)\u{201D}.")
                    .font(.system(size: 12))
                    .foregroundStyle(Palette.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            ZStack {
                TextField("", text: $code)
                    .textFieldStyle(.plain)
                    .textContentType(.oneTimeCode)
                    .focused($focused)
                    .frame(width: 1, height: 1)
                    .opacity(0)
                    .onChange(of: code) { typed in
                        let digits = String(typed.filter(\.isNumber).prefix(6))
                        if digits != typed { code = digits }
                    }
                    .onSubmit(submit)
                HStack(spacing: 10) {
                    ForEach(0..<6, id: \.self) { box($0) }
                }
                .frame(maxWidth: .infinity)
                .contentShape(Rectangle())
                .onTapGesture { focused = true }
            }
            HStack(alignment: .top, spacing: 10) {
                Text("!").font(.system(size: 12, weight: .bold)).foregroundStyle(Palette.warm)
                Text("Only enter a code your own phone is showing. Nobody else will ever need it.")
                    .font(.system(size: 12))
                    .foregroundStyle(Palette.ink)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 10).fill(Palette.warm.opacity(0.10)))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(Palette.warm.opacity(0.45), lineWidth: 1))
            HStack(spacing: 10) {
                Button("Cancel") { onDone(nil) }
                    .buttonStyle(FillButton(primary: false))
                    .keyboardShortcut(.cancelAction)
                Button("Pair", action: submit)
                    .buttonStyle(FillButton(primary: true))
                    .keyboardShortcut(.defaultAction)
                    .disabled(code.count != 6)
                    .opacity(code.count == 6 ? 1 : 0.45)
            }
        }
        .padding(28)
        .frame(width: 460)
        .background(Palette.ground)
        .onAppear { focused = true }
    }

    private func box(_ index: Int) -> some View {
        let digits = Array(code)
        let active = (focused || Args.snapshot != nil) && index == min(digits.count, 5) && digits.count < 6
        return Text(index < digits.count ? String(digits[index]) : "")
            .font(.system(size: 28, weight: .semibold, design: .monospaced))
            .foregroundStyle(Palette.ink)
            .frame(width: 56, height: 62)
            .background(RoundedRectangle(cornerRadius: 12).fill(Palette.raised))
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .stroke(active ? Palette.calm : Palette.line, lineWidth: active ? 2 : 1)
            )
            .shadow(color: active ? Palette.calm.opacity(0.45) : .clear, radius: 8)
    }

    private func submit() {
        guard code.count == 6 else { return }
        onDone(code)
    }
}

struct ResultView: View {
    let phone: String
    let paired: Bool
    var onDone: () -> Void = {}

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack(spacing: 8) {
                Step(number: "1", label: "Open Bell", state: -1)
                Step(number: "2", label: "Enter code", state: paired ? -1 : 0, accent: Palette.warm)
                Step(number: "3", label: "Done", state: paired ? 0 : 1, accent: Palette.ok)
            }
            VStack(alignment: .leading, spacing: 8) {
                Text(paired ? "Paired \u{2713}" : "That code didn\u{2019}t match")
                    .font(.system(size: 24, weight: .bold))
                    .foregroundStyle(paired ? Palette.ok : Palette.ink)
                Text(paired
                     ? "Letterbox Desk is paired with \u{201C}\(phone)\u{201D}. Your agents\u{2019} urgent mail will ring there."
                     : "Nothing was paired. Start again on the phone: it will show a new code.")
                    .font(.system(size: 13))
                    .foregroundStyle(Palette.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !paired {
                Button("OK", action: onDone)
                    .buttonStyle(FillButton(primary: true))
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(28)
        .frame(width: 460)
        .background(Palette.ground)
    }
}
