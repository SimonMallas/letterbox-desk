import SwiftUI

enum Palette {
    static let screen = Color(hex: 0x0b0d10)
    static let raised = Color(hex: 0x12151a)
    static let line = Color(hex: 0x252a31)
    static let raiseLine = Color(hex: 0x2e333b)
    static let ink = Color(hex: 0xeef1f5)
    static let ink2 = Color(hex: 0xc9d1da)
    static let muted = Color(hex: 0x9aa3ad)
    static let faint = Color(hex: 0x7f8893)
    static let calm = Color(hex: 0x9cc4ff)
    static let ring = Color(hex: 0xf5a524)
}

extension Color {
    init(hex: UInt32) {
        self.init(red: Double((hex >> 16) & 0xff) / 255,
                  green: Double((hex >> 8) & 0xff) / 255,
                  blue: Double(hex & 0xff) / 255)
    }
}

extension Font {
    static func ui(_ size: CGFloat, _ weight: Font.Weight = .regular) -> Font {
        .system(size: size, weight: weight)
    }

    static func mono(_ size: CGFloat, _ weight: Font.Weight = .regular) -> Font {
        .system(size: size, weight: weight, design: .monospaced)
    }
}

enum Clock {
    private static let hhmm: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_GB_POSIX")
        f.dateFormat = "HH:mm"
        return f
    }()

    private static let iso: ISO8601DateFormatter = ISO8601DateFormatter()

    private static let isoFractional: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    static func hhmm(_ date: Date) -> String { hhmm.string(from: date) }

    /// The envelope's `sent` field as local HH:MM, or nil if it isn't ISO 8601.
    static func hhmm(envelopeTS ts: String?) -> String? {
        guard let ts, let date = iso.date(from: ts) ?? isoFractional.date(from: ts) else { return nil }
        return hhmm(date)
    }
}

extension MailEnvelope {
    var shortID: String { String(id.suffix(8)) }
}

private struct SmallScreenKey: EnvironmentKey {
    static let defaultValue = false
}

extension EnvironmentValues {
    /// True on 667 pt phones (iPhone 6s/7/8/SE): tighter stage and caps so the buttons stay on screen.
    var smallScreen: Bool {
        get { self[SmallScreenKey.self] }
        set { self[SmallScreenKey.self] = newValue }
    }
}
