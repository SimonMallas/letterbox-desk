import AppKit
import Carbon
import SwiftUI

enum Args {
    static func value(_ flag: String) -> String? {
        let all = CommandLine.arguments
        guard let i = all.firstIndex(of: flag), i + 1 < all.count else { return nil }
        return all[i + 1]
    }
    static let phone = value("--phone") ?? "A phone"
    static let snapshot = value("--snapshot")
    static let preset = value("--preset") ?? ""
    static let result = value("--result")
    static let menubar = CommandLine.arguments.contains("--menubar")
    static let hint = CommandLine.arguments.contains("--hint")
    static let demo = value("--demo")
    static let slot = Int(value("--slot") ?? "0") ?? 0
}

struct Letter: Decodable {
    let from: String
    let type: String
    let ts: String
    let text: String
    var can_reply: Bool? = true

    static func fromStdin() -> Letter {
        let data = FileHandle.standardInput.readDataToEndOfFile()
        return (try? JSONDecoder().decode(Letter.self, from: data))
            ?? Letter(from: "An agent", type: "", ts: "", text: "", can_reply: false)
    }

    var time: String {
        guard let date = ISO8601DateFormatter().date(from: ts) else { return "" }
        let format = DateFormatter()
        format.dateFormat = "HH:mm"
        return format.string(from: date)
    }
}

enum Palette {
    static func shade(_ night: UInt32, _ day: UInt32) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            let value = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua ? night : day
            return NSColor(red: CGFloat((value >> 16) & 255)/255, green: CGFloat((value >> 8) & 255)/255,
                           blue: CGFloat(value & 255)/255, alpha: 1)
        })
    }
    static let ground = shade(0x0e1014, 0xfcfcf5)
    static let raised = shade(0x161a20, 0xffffff)
    static let line = shade(0x2a3038, 0xe0dac8)
    static let ink = shade(0xeef1f5, 0x1c1b1b)
    static let muted = shade(0x9aa3ad, 0x6b6455)
    static let calm = shade(0x9cc4ff, 0x2f6fd6)
    static let warm = shade(0xf5a524, 0xb16a05)
    static let calmInk = shade(0x0b0d10, 0xffffff)
    static let ok = shade(0x8fd6a4, 0x246d3e)
}

struct Step: View {
    let number: String
    let label: String
    let state: Int
    var accent: Color = Palette.calm

    var body: some View {
        HStack(spacing: 6) {
            Text(state < 0 ? "✓" : number).font(.system(size: 11, weight: .semibold, design: .monospaced))
            Text(label).font(.system(size: 11, weight: .medium))
        }
        .foregroundStyle(state == 0 ? Palette.ink : Palette.muted)
        .padding(.horizontal, 10)
        .frame(height: 28)
        .frame(maxWidth: .infinity)
        .background(RoundedRectangle(cornerRadius: 8).fill(state == 0 ? accent.opacity(0.14) : Palette.raised))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(state == 0 ? accent : Palette.line, lineWidth: 1))
    }
}

struct FillButton: ButtonStyle {
    let primary: Bool

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 14, weight: .semibold))
            .foregroundStyle(primary ? Palette.calmInk : Palette.ink)
            .frame(maxWidth: .infinity)
            .frame(height: 40)
            .background(RoundedRectangle(cornerRadius: 10).fill(primary ? Palette.calm : Palette.raised))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(primary ? Color.clear : Palette.line, lineWidth: 1))
            .opacity(configuration.isPressed ? 0.8 : 1)
    }
}
