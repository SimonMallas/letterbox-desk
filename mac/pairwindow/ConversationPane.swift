import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct ChatPosition: Equatable {
    var bottom: CGFloat
    var height: CGFloat
}

struct ChatScrollState {
    var atBottom = true
    var height: CGFloat?
    mutating func update(_ position: ChatPosition) -> Bool {
        let pin = height != nil && height != position.height && atBottom
        height = position.height
        if !pin { atBottom = position.bottom <= position.height + 30 }
        return pin
    }
}

struct BottomPosition: PreferenceKey {
    static var defaultValue = ChatPosition(bottom: 0, height: 0)
    static func reduce(value: inout ChatPosition, nextValue: () -> ChatPosition) { value = nextValue() }
}

struct BellFigure: View {
    var size: CGFloat = 44
    private func image(_ name: String) -> NSImage? {
        let base = Args.value("--art-dir").map { URL(fileURLWithPath: $0) }
            ?? Bundle.main.resourceURL?.appendingPathComponent("Art")
            ?? URL(fileURLWithPath: CommandLine.arguments[0]).deletingLastPathComponent().appendingPathComponent("../assets")
        return NSImage(contentsOf: base.appendingPathComponent(name + ".png"))
    }
    var body: some View {
        ZStack {
            ForEach(["body", "eye_L", "eye_R", "mouth_smile"], id: \.self) { name in
                if let img = image(name) { Image(nsImage: img).resizable().scaledToFit() }
            }
        }.frame(width: size, height: size).accessibilityLabel("Letterbox Bell")
    }
}

struct ConversationPane: View {
    @ObservedObject var model: DeskModel
    @State private var scroll = ChatScrollState()
    @State private var hasNew = false
    @State private var pictureError = ""
    private var text: Binding<String> { Binding(get: {model.draft.text}, set: { v in model.editDraft {$0.text = v} }) }
    private var messages: [ConversationMessage] { model.conversation?.messages ?? [] }
    private func toggle(_ seat: String) {
        model.editDraft { value in
            if value.recipients.contains(seat) {value.recipients.removeAll {$0 == seat}}
            else {value.recipients.append(seat)}
        }
    }
    private func choosePicture(_ url: URL) {
        guard ["png", "jpg", "jpeg"].contains(url.pathExtension.lowercased()),
              let data = try? DraftPicture.read(url) else {
            pictureError = "Choose a PNG or JPEG, at most 10 MB, without a symbolic link."; return
        }
        // Capture temporary screenshot bytes now; the source may disappear before Send.
        model.editDraft {$0.picture = PictureDraft(name: url.lastPathComponent, size: data.count, payload: ["data":data.base64EncodedString()])}
        pictureError = ""
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(model.agent ?? "New letter").font(.system(size: 21, weight: .semibold, design: .monospaced))
                    Text(model.agent == nil ? "Write to one or more agents." : "Your conversation · last \(model.keepDays) days")
                        .font(.system(size: 12)).foregroundStyle(Palette.muted)
                }
                Spacer()
            }.padding(.top, 35).padding(.trailing, 120)
            HStack(spacing: 7) {
                Text("TO").font(.system(size: 10, design: .monospaced)).foregroundStyle(Palette.muted)
                ForEach(model.seats, id: \.self) { seat in
                    Button {toggle(seat)} label: {
                        VStack(spacing: 1) {
                            Text(seat).font(.system(size: 12, design: .monospaced))
                            if seat == model.agent {Text(model.conversation?.latest.isEmpty == false ? "reply" : "new letter").font(.system(size: 9))}
                            else if model.draft.recipients.contains(seat) {Text("new letter").font(.system(size: 9))}
                        }.padding(.horizontal, 12).frame(height: 35)
                            .foregroundStyle(model.draft.recipients.contains(seat) ? Palette.ink : Palette.muted)
                            .background(RoundedRectangle(cornerRadius: 8).fill(model.draft.recipients.contains(seat) ? Palette.calm.opacity(0.15) : Palette.raised))
                            .overlay(RoundedRectangle(cornerRadius: 8).stroke(model.draft.recipients.contains(seat) ? Palette.calm : Palette.line))
                    }.buttonStyle(.plain).disabled(model.conversationSending == model.draftKey)
                }
            }
            if model.agent != nil { conversation }
            Editor(text: text, placeholder: "Write your letter…  Drag a picture here", minHeight: model.agent == nil ? 180 : 65, onPicture: { data, url in
                    if let url {choosePicture(url)}
                    else if let data {
                        guard data.count <= 10*1024*1024 else {pictureError = "Pictures must be at most 10 MB."; return}
                        model.editDraft {$0.picture = PictureDraft(name: "Pasted picture", size: data.count, payload: ["data":data.base64EncodedString()])}
                    }
                })
                .frame(height: model.agent == nil ? 200 : 100)
                .disabled(model.conversationSending == model.draftKey)
                .onPasteCommand(of: [.png, .jpeg, .fileURL]) { providers in
                    let key = model.draftKey
                    guard let provider = providers.first else {return}
                    if provider.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier) {
                        provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier, options: nil) { value, _ in
                            let url = (value as? URL) ?? (value as? Data).flatMap {URL(dataRepresentation: $0, relativeTo: nil)}
                            if let url {DispatchQueue.main.async {if model.draftKey == key {choosePicture(url)}}}
                        }
                    } else {
                        let type = provider.hasItemConformingToTypeIdentifier(UTType.png.identifier) ? UTType.png.identifier : UTType.jpeg.identifier
                        provider.loadDataRepresentation(forTypeIdentifier: type) { data, _ in
                            guard let data, data.count <= 10*1024*1024 else {return}
                            DispatchQueue.main.async {if model.draftKey == key {
                                model.editDraft {$0.picture = PictureDraft(name: type == UTType.png.identifier ? "Pasted picture.png" : "Pasted picture.jpg", size: data.count, payload: ["data":data.base64EncodedString()])}
                            }}
                        }
                    }
                }
                .onDrop(of: [UTType.fileURL.identifier], isTargeted: nil) { providers in
                    guard let provider = providers.first else {return false}
                    let key = model.draftKey
                    provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier, options: nil) { value, _ in
                        let url = (value as? URL) ?? (value as? Data).flatMap { URL(dataRepresentation: $0, relativeTo: nil) }
                        if let url { DispatchQueue.main.async {if model.draftKey == key {choosePicture(url)}} }
                    }; return true
                }
            HStack(spacing: 10) {
                if let picture = model.draft.picture {
                    Text("\(picture.name) · \(ByteCountFormatter.string(fromByteCount: Int64(picture.size), countStyle: .file))").lineLimit(1)
                    Button("Remove") {model.editDraft {$0.picture = nil}}
                }
            }.font(.system(size: 11)).foregroundStyle(Palette.muted).disabled(model.conversationSending == model.draftKey)
            if !pictureError.isEmpty {Text(pictureError).font(.system(size: 11)).foregroundStyle(Palette.warm)}
            VStack(alignment: .leading, spacing: 3) {
                Text("WHAT GOES OUT").font(.system(size: 9, weight: .semibold, design: .monospaced)).tracking(1.2)
                ForEach(model.draft.recipients, id: \.self) { seat in
                    Text(seat == model.agent && model.conversation?.latest.isEmpty == false
                         ? "\(seat) · a reply in this conversation: exactly what you typed\(model.draft.picture == nil ? "" : ", with the picture")"
                         : "\(seat) · a new letter: this message\(model.draft.picture == nil ? "" : " and the picture") only, not the conversation")
                }
                if model.agent != nil && !model.draft.recipients.contains(model.agent!) {Text("This does not answer the open conversation.").foregroundStyle(Palette.warm)}
            }.font(.system(size: 10, design: .monospaced)).foregroundStyle(Palette.muted)
                .padding(10).frame(maxWidth: .infinity, alignment: .leading)
                .background(RoundedRectangle(cornerRadius: 8).fill(Palette.raised))
            HStack {
                if let summary = model.conversationResults[model.draftKey] {
                    VStack(alignment: .leading) {
                        Text(Words.title(summary.status)).font(.system(size: 12, weight: .semibold))
                        OutcomeLines(summary: summary)
                        ForEach(model.pictureSetupSeats, id: \.self) { seat in
                            Button("Ask \(seat) to set up pictures") {model.askPictureSetup(seat)}
                                .disabled(model.pictureSetupStatus[seat] == "Sending request…")
                        }
                        ForEach(model.pictureSetupStatus.keys.sorted(), id: \.self) { seat in
                            Text("\(seat): \(model.pictureSetupStatus[seat] ?? "")").font(.system(size: 10))
                        }
                        if model.canSendWithoutPicture {
                            Button("Send without the picture") {model.sendWithoutPicture()}
                                .disabled(model.conversationSending != nil)
                        } else if summary.status != "sent" {Text("Retry unconfirmed copies in Sent; confirmed copies are never resent.").font(.system(size: 10))}
                    }
                }
                Spacer()
                if model.agent != nil {Button("Done — keep files") {model.doneConversation()}.buttonStyle(.bordered)}
                Text("\(model.draft.text.count)/2,000").font(.system(size: 10, design: .monospaced)).foregroundStyle(Palette.muted)
                Button(model.conversationSending == nil ? "Send" : "Sending…") {model.sendConversation()}
                    .buttonStyle(FillButton(primary: true)).frame(width: 120)
                    .keyboardShortcut(.return, modifiers: .command)
                    .disabled(model.conversationSending != nil || model.draft.recipients.isEmpty || model.draft.text.count > 2000 || (model.draft.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && model.draft.picture == nil))
            }
        }.padding(.horizontal, 24).padding(.bottom, 20).foregroundStyle(Palette.ink)
            .overlay(alignment: .topTrailing) {
                BellSwitch(model: model).padding(.top, 18).padding(.trailing, 20)
            }
    }
    private var conversation: some View {
        GeometryReader { outer in
            ScrollViewReader { proxy in
                ZStack(alignment: .bottom) {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 10) {
                            ForEach(messages) { message in
                                if model.newIDs.contains(message.id) && messages.first(where: {model.newIDs.contains($0.id)})?.id == message.id {
                                    Text("NEW").font(.system(size: 9, weight: .bold, design: .monospaced)).foregroundStyle(Palette.calm).frame(maxWidth: .infinity)
                                }
                                HStack {
                                    if message.direction == "out" {Spacer(minLength: 45)}
                                    VStack(alignment: .leading, spacing: 5) {
                                        Text("\(message.direction == "out" ? "desk" : (model.agent ?? "")) · \(Words.when(Date(timeIntervalSince1970: message.at)))\(message.via == "phone" ? " · phone" : "")")
                                            .font(.system(size: 10, design: .monospaced)).foregroundStyle(Palette.muted)
                                        Text(verbatim: message.text).font(.system(size: 13)).textSelection(.enabled)
                                        if let status = message.picture_status {Text("Picture: \(status)").font(.system(size: 10)).foregroundStyle(status == "ok" ? Palette.muted : Palette.warm)}
                                        if let status = message.status, status != "published" {Text(Words.title(status)).font(.system(size: 10)).foregroundStyle(Palette.warm)}
                                    }.padding(12).background(RoundedRectangle(cornerRadius: 10).fill(message.direction == "out" ? Palette.calm.opacity(0.12) : Palette.raised))
                                    if message.direction == "in" {Spacer(minLength: 45)}
                                }.id(message.id)
                            }
                            Color.clear.frame(height: 1).id("bottom").background(GeometryReader { g in Color.clear.preference(key: BottomPosition.self, value: ChatPosition(bottom: g.frame(in: .named("chat")).maxY, height: outer.size.height)) })
                        }.padding(12)
                    }.coordinateSpace(name: "chat")
                    .onPreferenceChange(BottomPosition.self) { position in
                        if scroll.update(position) {DispatchQueue.main.async {proxy.scrollTo("bottom", anchor: .bottom)}}
                    }
                    .onAppear {proxy.scrollTo("bottom", anchor: .bottom)}
                    .onChange(of: model.agent) { _ in scroll.atBottom = true; hasNew = false; proxy.scrollTo("bottom", anchor: .bottom)}
                    .onChange(of: messages.last?.id) { _ in
                        if scroll.atBottom {DispatchQueue.main.async {
                            proxy.scrollTo("bottom", anchor: .bottom)
                            if let agent = model.agent, NSApp.keyWindow?.isVisible == true {model.write(["conversation":["action":"read","seat":agent]])}
                        }}
                        else {hasNew = true}
                    }
                    if hasNew && !scroll.atBottom {Button("New messages ↓") {proxy.scrollTo("bottom", anchor: .bottom); hasNew = false}.buttonStyle(.borderedProminent).padding(8)}
                }
            }
        }.frame(minHeight: 140).background(Palette.raised.opacity(0.5))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(Palette.line))
    }
}

struct BellSwitch: View {
    @ObservedObject var model: DeskModel
    var body: some View {
                Button {model.setting(["rings":!model.rings])} label: {
                    VStack(spacing: 3) {
                        BellFigure(size: 80).grayscale(model.rings ? 0 : 1).opacity(model.rings ? 1 : 0.4)
                            .background(Color(red: 0.04, green: 0.05, blue: 0.06)).clipShape(RoundedRectangle(cornerRadius: 13))
                        Text(model.rings ? "On" : "Off").font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(model.rings ? Palette.ok : Palette.muted)
                    }.padding(8).background(RoundedRectangle(cornerRadius: 16).fill(Palette.raised))
                        .overlay(RoundedRectangle(cornerRadius: 16).stroke(Palette.line))
                }.buttonStyle(.plain)
                    .accessibilityLabel("Rings on new mail: " + (model.rings ? "On" : "Off"))
                    .help("Press to switch new-mail ringing. The phone stays connected.")
    }
}
