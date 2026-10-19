import Foundation

/// When a ring-eligible letter should actually ring. Foundation only, so it can be checked outside the app.
struct RingPolicy: Equatable {
    enum RingFor: String, CaseIterable {
        case everything
        case repliesOnly
    }

    var ringFor: RingFor = .everything
    var quietHours = false
    /// Minutes after midnight, local time.
    var quietFrom = 22 * 60
    var quietTo = 7 * 60

    func shouldRing(forYou: Bool, at date: Date, calendar: Calendar = .current) -> Bool {
        if ringFor == .repliesOnly && !forYou { return false }
        if quietHours {
            let parts = calendar.dateComponents([.hour, .minute], from: date)
            if isQuiet(minute: (parts.hour ?? 0) * 60 + (parts.minute ?? 0)) { return false }
        }
        return true
    }

    /// The span may cross midnight (22:00–07:00). Equal ends mean no quiet time.
    func isQuiet(minute: Int) -> Bool {
        if quietFrom == quietTo { return false }
        if quietFrom < quietTo { return minute >= quietFrom && minute < quietTo }
        return minute >= quietFrom || minute < quietTo
    }
}

/// Plain words for an envelope's letter_type.
enum LetterWords {
    /// "a new task", "results", "an update"…
    static func phrase(_ letterType: String) -> String {
        switch letterType {
        case "request", "delegate": return "a new task"
        case "result": return "results"
        case "info": return "an update"
        case "ack": return "an OK"
        case "nack": return "a no"
        case "question": return "a question"
        default: return "mail"
        }
    }

    /// The phrase without its article, capitalised: "New task", "Results", "OK".
    static func word(_ letterType: String) -> String {
        var p = phrase(letterType)
        for article in ["a ", "an "] where p.hasPrefix(article) {
            p = String(p.dropFirst(article.count))
        }
        return p.prefix(1).uppercased() + p.dropFirst()
    }
}
