import SwiftUI
struct Sidebar: View {
    @ObservedObject var model: DeskModel
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("LETTERBOX DESK").font(.system(size: 10, weight: .semibold, design: .monospaced)).tracking(1.6).foregroundStyle(Palette.muted).padding(.bottom, 14)
            nav("＋ New letter", on: model.section == .compose) {model.chooseAgent(nil)}
            Text("AGENTS").font(.system(size: 9, design: .monospaced)).foregroundStyle(Palette.muted).padding(.top, 14)
            ForEach(model.seats, id: \.self) { seat in
                let conversation = model.conversations.first {$0.seat == seat}
                if conversation?.done != true || model.showDone {
                    nav(seat + ((conversation?.unread ?? 0) > 0 ? "   \(conversation!.unread)" : "") + (conversation?.done == true ? " · done" : ""), on: model.agent == seat && model.section == .inbox) {
                        model.chooseAgent(seat)
                        if conversation?.done == true {model.write(["conversation":["action":"restore","seat":seat]])}
                    }
                }
            }
            Divider().padding(.vertical, 8)
            nav("Sent", on: model.section == .sent) {model.section = .sent}
            Button("\(model.showDone ? "Hide done" : "Show done") (\(model.conversations.filter(\.done).count))") {model.showDone.toggle()}.buttonStyle(.plain).foregroundStyle(Palette.calm).font(.system(size: 12))
            Spacer()
            HStack {
                Text("Keep"); Spacer()
                Button("−") {model.setting(["keep":max(1,model.keepDays-1)])}.disabled(model.keepDays <= 1)
                Text("\(model.keepDays) days").monospacedDigit()
                Button("+") {model.setting(["keep":model.keepDays+1])}
            }.font(.system(size: 11))
            Picker("Look", selection: Binding(get: {model.look}, set: {v in model.setting(["look":v])})) {
                Text("Day").tag("day"); Text("Auto").tag("auto"); Text("Night").tag("night")
            }.pickerStyle(.segmented).labelsHidden().padding(.vertical, 8)
            Text("Your letters go out as “desk”.").font(.system(size: 11)).foregroundStyle(Palette.muted).fixedSize(horizontal: false, vertical: true)
        }.padding(.top, 44).padding(.horizontal, 18).padding(.bottom, 18)
            .frame(maxHeight: .infinity, alignment: .top).background(Palette.raised.opacity(0.6)).foregroundStyle(Palette.ink)
    }
    private func nav(_ title: String, on: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {Text(title).font(.system(size: 13, weight: on ? .semibold : .regular)).frame(maxWidth: .infinity, alignment: .leading).padding(9).background(RoundedRectangle(cornerRadius: 8).fill(on ? Palette.calm.opacity(0.14) : Color.clear))}.buttonStyle(.plain)
    }
}
