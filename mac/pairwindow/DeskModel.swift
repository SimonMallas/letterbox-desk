import AppKit
import Carbon
import SwiftUI

struct Ring: Decodable, Equatable {
    let outcome: String
    let reason: String?
}

struct SeatOutcome: Decodable, Equatable {
    let to: String
    let status: String
    let id: String?
    let reason: String?
    let ring: Ring?
}

struct Summary: Decodable, Equatable {
    let status: String
    let reason: String?
    let request_id: String?
    let results: [SeatOutcome]?
}

struct InboxLetter: Decodable, Identifiable, Equatable {
    let id: String
    let from: String
    let type: String
    let ts: String
    let re: String
    let preview: String
    let read: Bool
    let can_reply: Bool
}

struct OpenLetter: Decodable, Equatable {
    let id: String
    let from: String
    let type: String
    let ts: String
    let re: String
    let text: String
    let can_reply: Bool
}

struct SentLetter: Decodable, Identifiable, Equatable {
    let request_id: String
    let at: Int
    let via: String
    let re: String
    let text: String?
    let status: String
    let results: [SeatOutcome]
    var id: String { request_id }
}

struct TeamSeat: Decodable, Identifiable {
    let seat: String
    let unacked: Int?
    let accepted: Int?
    let reported: String
    let has_status: Bool
    var id: String { seat }
}

struct Team: Decodable {
    let ok: Bool
    let checked: Int
    let tried: Int
    let seats: [TeamSeat]
}

struct MailMessage: Decodable {
    let inbox: [InboxLetter]
    let sent: [SentLetter]
}

struct LetterMessage: Decodable {
    let id: String
    let letter: OpenLetter?
}

struct ResultMessage: Decodable {
    let kind: String
    let ref: String
    let summary: Summary
}

enum Words {
    static let maxText = 2000

    static func reason(_ code: String?) -> String {
        switch code ?? "" {
        case "picture_access", "pictures_not_configured": return "Pictures are not set up yet: remove the picture to send the text"
        case "text_empty": return "there was nothing to send"
        case "text_too_long": return "it is longer than 2,000 characters"
        case "text_control_chars": return "it contains hidden control characters"
        case "unknown_seat": return "that agent isn\u{2019}t one of yours"
        case "inbox_missing": return "an agent\u{2019}s inbox is missing"
        case "helper_changed": return "the Letterbox helper changed since it was pinned"
        case "helper_unavailable": return "the Letterbox helper couldn\u{2019}t be run"
        case "helper_timeout": return "the Letterbox helper took too long to answer"
        case "shutting_down": return "the listener was stopping"
        case "already_delivered": return "that copy already arrived"
        case "still_sending": return "the first send is still running"
        case "unknown_letter": return "that letter is no longer here"
        case "interrupted": return "the listener stopped part-way"
        case let other where other.hasPrefix("helper_exit_"): return "the Letterbox helper didn\u{2019}t confirm it"
        case let other: return other.isEmpty ? "unknown reason" : other
        }
    }

    /// One plain line per seat, and whether that copy is known to be in the inbox.
    static func seat(_ r: SeatOutcome) -> (String, Bool) {
        switch r.status {
        case "published":
            let rang = r.ring?.outcome == "submitted"
            return ("In \(r.to)\u{2019}s inbox \u{00B7} " + (rang ? "doorbell rang" : "the doorbell didn\u{2019}t ring"), true)
        case "not_confirmed":
            return ("\(r.to): the Mac couldn\u{2019}t confirm it (it may have arrived). Not retried.", false)
        case "not_attempted":
            return ("\(r.to): not attempted, because \(reason(r.reason)).", false)
        case "sending":
            return ("\(r.to): sending\u{2026}", false)
        default:
            return ("\(r.to): not sent.", false)
        }
    }

    static func lines(_ s: Summary) -> [(String, Bool)] {
        if ["picture_access", "pictures_not_configured"].contains(s.reason ?? ""), s.status == "not_sent" {
            return [(reason(s.reason), false)]
        }
        if let results = s.results, !results.isEmpty { return results.map(seat) }
        return [("Not sent: \(reason(s.reason)).", false)]
    }

    static func title(_ status: String) -> String {
        switch status {
        case "sent": return "Sent \u{2713}"
        case "partial": return "Partly sent"
        case "not_confirmed": return "Not confirmed"
        case "sending": return "Sending\u{2026}"
        default: return "Not sent"
        }
    }

    static func date(_ iso: String) -> Date? {
        ISO8601DateFormatter().date(from: iso)
    }

    static func when(_ date: Date?) -> String {
        guard let date else { return "" }
        let format = DateFormatter()
        format.dateFormat = Calendar.current.isDateInToday(date) ? "HH:mm" : "d MMM, HH:mm"
        return format.string(from: date)
    }
}

enum DeskSection: Hashable {
    case inbox, compose, sent
}

final class DeskModel: ObservableObject {
    func loadFixture() {
        guard let path = Args.value("--fixture"), let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
              let snapshot = try? JSONDecoder().decode(ConversationSnapshot.self, from: data) else {return}
        seats = snapshot.conversations.map(\.seat); receive(snapshot)
        openDesk()
        if let draft = snapshot.draft {
            drafts[draftKey] = ConversationDraft(text: draft.text, recipients: draft.to,
                picture: draft.picture_name.map {PictureDraft(name: $0, size: 245760, payload: [:])})
        }
        if let mode = Args.value("--look") {look = mode}
    }
    @Published var conversations: [Conversation] = []
    @Published var agent: String?
    var awaitingDeskSelection = false
    @Published var showDone = false
    @Published var keepDays = 3
    @Published var look = "auto"
    @Published var rings = true
    @Published var drafts: [String: ConversationDraft] = [:]
    @Published var conversationSending: String?
    @Published var conversationResults: [String: Summary] = [:]
    @Published var pictureReaders: Set<String> = []
    @Published var pictureSetupStatus: [String:String] = [:]
    @Published var newIDs: Set<String> = []
    var draftKey: String { agent ?? "new" }
    var conversation: Conversation? { conversations.first { $0.seat == agent } }
    var draft: ConversationDraft { drafts[draftKey] ?? ConversationDraft(recipients: agent.map { [$0] } ?? []) }
    func editDraft(_ edit: (inout ConversationDraft) -> Void) {
        var value = draft; edit(&value); drafts[draftKey] = value
    }
    func chooseAgent(_ seat: String?) {
        awaitingDeskSelection = false
        agent = seat; section = seat == nil ? .compose : .inbox
        newIDs = Set(conversation?.messages.filter { !$0.read }.map(\.id) ?? [])
        if let seat { write(["conversation": ["action": "read", "seat": seat]]) }
        else {write(["conversation": ["action": "close"]])}
    }
    func preferredAgent() -> String? {
        let active = conversations.filter { !$0.messages.isEmpty || !$0.latest.isEmpty || ($0.last_at ?? 0) > 0 }
        let unread = active.filter { $0.unread > 0 }
        func time(_ row: Conversation) -> Double {
            if !unread.isEmpty { return row.messages.filter {!$0.read}.map(\.at).max() ?? 0 }
            return row.last_at ?? row.messages.map(\.at).max() ?? 0
        }
        return (unread.isEmpty ? active : unread).max {
            time($0) == time($1) ? $0.seat < $1.seat : time($0) < time($1)
        }?.seat
    }
    func openDesk() {
        if let seat = preferredAgent() {
            chooseAgent(seat)
            if conversation?.done == true {write(["conversation":["action":"restore","seat":seat]])}
        } else {
            agent = nil; section = .inbox; newIDs = []; awaitingDeskSelection = true
        }
    }
    func setting(_ fields: [String: Any]) {
        write(["conversation": fields.merging(["action": "settings"]) { _, v in v }])
    }
    func doneConversation() {
        guard let agent else { return }
        write(["conversation": ["action": "done", "seat": agent]])
        chooseAgent(nil)
    }
    var pictureSetupSeats: [String] {
        guard draft.picture != nil else {return []}
        return (conversationResults[draftKey]?.results ?? []).filter {
            $0.status == "not_attempted" && $0.reason == "picture_access" && !pictureReaders.contains($0.to)
        }.map(\.to)
    }
    func askPictureSetup(_ seat: String) {
        guard pictureSetupSeats.contains(seat), pictureSetupStatus[seat] != "Sending request…" else {return}
        pictureSetupStatus[seat] = "Sending request…"
        write(["picture_setup": ["seat":seat]])
    }
    var canSendWithoutPicture: Bool {
        guard conversationSending == nil, draft.picture != nil, !draft.recipients.isEmpty,
              !draft.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, draft.text.count <= Words.maxText,
              let summary = conversationResults[draftKey], summary.status == "not_sent", summary.request_id == nil,
              ["picture_access", "pictures_not_configured"].contains(summary.reason ?? "") else {return false}
        return (summary.results ?? []).allSatisfy {$0.status == "not_attempted"}
    }
    func sendWithoutPicture() {
        guard canSendWithoutPicture else {return}
        editDraft {$0.picture = nil}
        sendConversation()
    }
    func sendConversation() {
        guard conversationSending == nil, !draft.recipients.isEmpty else { return }
        let key = draftKey
        var packet: [String: Any] = ["to": draft.recipients, "text": draft.text]
        if let agent { packet["agent"] = agent }
        if let picture = draft.picture { packet["picture"] = picture.payload }
        conversationSending = key; conversationResults[key] = nil
        write(["conversation_send": packet])
    }
    func receive(_ snapshot: ConversationSnapshot) {
        conversations = snapshot.conversations
        if awaitingDeskSelection && section == .inbox && agent == nil && preferredAgent() != nil {openDesk()}
        if let conversation {newIDs.formUnion(conversation.messages.filter {!$0.read}.map(\.id))}
        pictureReaders = Set(snapshot.picture_readers ?? [])
        for seat in Array(pictureSetupStatus.keys) where pictureReaders.contains(seat) {
            pictureSetupStatus[seat] = "Pictures ready. You can send your draft."
        }
        keepDays = snapshot.keep; look = snapshot.look; rings = snapshot.rings
    }
    @Published var section: DeskSection = .inbox
    @Published var seats: [String] = []
    @Published var inbox: [InboxLetter] = []
    @Published var sent: [SentLetter] = []
    @Published var selected: String?
    @Published var opened: OpenLetter?
    @Published var missing = false
    @Published var replyDrafts: [String: String] = [:]
    @Published var replySending: String?
    @Published var replyResults: [String: Summary] = [:]
    @Published var composeTo: [String] = []
    @Published var composeText = ""
    @Published var composeSending = false
    @Published var composeResult: Summary?
    @Published var resending: Set<String> = []
    @Published var resendResults: [String: Summary] = [:]
    var write: ([String: Any]) -> Void = { _ in }

    var unread: Int { inbox.filter { !$0.read }.count }

    func open(_ id: String) {
        selected = id
        missing = false
        if opened?.id != id { opened = nil }
        write(["open": id])
    }

    func sendReply(to letter: OpenLetter) {
        let text = replyDrafts[letter.id] ?? ""
        guard replySending == nil, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        replySending = letter.id
        replyResults[letter.id] = nil
        write(["reply": ["id": letter.id, "text": text]])
    }

    func sendCompose() {
        guard !composeSending, !composeTo.isEmpty, !composeText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        composeSending = true
        composeResult = nil
        write(["compose": ["to": composeTo, "text": composeText]])
    }

    func resend(_ item: SentLetter, _ seat: String) {
        let key = "\(item.request_id)/\(seat)"
        guard !resending.contains(key) else { return }
        resending.insert(key)
        resendResults[key] = nil
        write(["resend": ["request_id": item.request_id, "seat": seat]])
    }

    func receive(_ message: LetterMessage) {
        guard let letter = message.letter else {
            if selected != nil && opened == nil { missing = true }
            return
        }
        if letter.id == selected { opened = letter }
    }

    func receive(_ message: ResultMessage) {
        switch message.kind {
        case "picture_setup":
            pictureSetupStatus[message.ref] = message.summary.status == "sent"
                ? (pictureReaders.contains(message.ref) ? "Pictures ready. You can send your draft." : "Request sent. Waiting for the agent to read it.")
                : "Request not sent. Try again."
        case "conversation":
            let key = message.ref.isEmpty ? "new" : message.ref
            conversationSending = nil; conversationResults[key] = message.summary
            if message.summary.request_id != nil {
                drafts[key] = ConversationDraft(recipients: key == "new" ? [] : [key])
            }

        case "compose":
            composeSending = false
            composeResult = message.summary
            if message.summary.status == "sent" {
                composeText = ""
                composeTo = []
            }
        case "reply":
            replySending = nil
            replyResults[message.ref] = message.summary
            if message.summary.status == "sent" { replyDrafts[message.ref] = nil }
        case "resend":
            resending.remove(message.ref)
            resendResults[message.ref] = message.summary
        default:
            break
        }
    }
}


struct ConversationMessage: Decodable, Identifiable, Equatable {
    let id: String
    let direction: String
    let text: String
    let at: Double
    let read: Bool
    let via: String
    let status: String?
    let request_id: String?
    let picture_status: String?
}
struct Conversation: Decodable, Identifiable, Equatable {
    let seat: String
    let latest: String
    let last_at: Double?
    let done: Bool
    let unread: Int
    let messages: [ConversationMessage]
    var id: String { seat }
}
struct FixtureDraft: Decodable {
    let text: String
    let to: [String]
    let picture_name: String?
}
struct ConversationSnapshot: Decodable {
    let draft: FixtureDraft?
    let picture_readers: [String]?
    let conversations: [Conversation]
    let keep: Int
    let look: String
    let rings: Bool
}
struct PictureDraft {
    var name: String
    var size: Int
    var payload: [String: String]
}
struct ConversationDraft {
    var text = ""
    var recipients: [String] = []
    var picture: PictureDraft?
}
