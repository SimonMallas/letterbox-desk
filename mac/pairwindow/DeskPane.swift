import AppKit
import Carbon
import SwiftUI

struct DeskView: View {
    @ObservedObject var model: DeskModel

    var body: some View {
        HStack(spacing: 0) {
            Sidebar(model: model).frame(width: 200)
            Rectangle().fill(Palette.line).frame(width: 1)
            Group {
                switch model.section {
                case .inbox:
                    if model.agent == nil {
                        VStack(alignment: .leading, spacing: 12) {
                            Text("Letterbox Desk").font(.system(size: 24, weight: .semibold))
                            Text("Choose an agent, or press ＋ New letter to write.").foregroundStyle(Palette.muted)
                            Spacer()
                        }.frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                            .padding(28).padding(.top, 24).foregroundStyle(Palette.ink)
                            .overlay(alignment: .topTrailing) {BellSwitch(model: model).padding(.top, 18).padding(.trailing, 20)}
                    } else {ConversationPane(model: model)}
                case .compose: ConversationPane(model: model)
                case .sent: SentPane(model: model)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
        .frame(minWidth: 780, minHeight: 500)
        .background(Palette.ground)
        .preferredColorScheme(model.look == "day" ? .light : model.look == "night" ? .dark : nil)
        .ignoresSafeArea()
    }
}

struct ResultsView: View {
    let title: String
    let ok: Bool
    let summary: Summary
    var onDone: () -> Void = {}

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.system(size: 17, weight: .semibold))
                .foregroundStyle(ok ? Palette.ok : Palette.warm)
            OutcomeLines(summary: summary)
            if !ok {
                Button("OK", action: onDone).buttonStyle(FillButton(primary: true)).padding(.top, 6)
            }
        }
        .padding(20)
        .frame(width: 460, alignment: .leading)
        .fixedSize(horizontal: false, vertical: true)
        .background(Palette.ground)
    }
}

struct HintView: View {
    var onDone: () -> Void = {}

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("\u{2191} Letterbox Desk lives here")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(Palette.ink)
            Text("Click the bell for your Letterbox Desk, or press \u{2325}\u{2318}B from anywhere to write to your agents.")
                .font(.system(size: 12))
                .foregroundStyle(Palette.muted)
                .fixedSize(horizontal: false, vertical: true)
            Button("Got it", action: onDone)
                .buttonStyle(FillButton(primary: true))
        }
        .padding(16)
        .frame(width: 280)
        .background(RoundedRectangle(cornerRadius: 14).fill(Palette.ground))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Palette.calm, lineWidth: 1))
    }
}
