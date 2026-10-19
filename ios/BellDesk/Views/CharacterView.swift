import SwiftUI
import UIKit

/// The character from the delivered layers (body, one mouth, eye_L, eye_R), stacked on one 1179×986 canvas.
/// All motion is translate / rotate / scaleY on the eyes; the mouth only ever swaps.
struct CharacterView: View {
    let face: AppModel.Face

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var life = CharacterLife()

    static let size = CGSize(width: 300, height: 300 * 986.0 / 1179)
    /// Top of the hanger loop: the swing pivot.
    static let hang = UnitPoint(x: 592.0 / 1179, y: 20.0 / 986)
    static let pivotL = UnitPoint(x: 508.0 / 1179, y: 332.0 / 986)
    static let pivotR = UnitPoint(x: 670.0 / 1179, y: 332.0 / 986)

    var body: some View {
        TimelineView(.animation(minimumInterval: nil, paused: reduceMotion)) { context in
            layers(reduceMotion ? CharacterLife.still(face) : life.pose(at: context.date, face: face))
        }
        .frame(width: Self.size.width, height: Self.size.height)
        .accessibilityElement()
        .accessibilityLabel("The bell holding a glowing letter")
    }

    private func layers(_ pose: CharacterLife.Pose) -> some View {
        ZStack {
            art(Art.body)
            art(Art.mouth(pose.mouth))
            art(Art.eyeL)
                .scaleEffect(x: 1, y: pose.lidL, anchor: Self.pivotL)
                .offset(x: pose.eyes.width + pose.cross, y: pose.eyes.height)
            art(Art.eyeR)
                .scaleEffect(x: 1, y: pose.lidR, anchor: Self.pivotR)
                .offset(x: pose.eyes.width - pose.cross, y: pose.eyes.height)
        }
        .frame(width: Self.size.width, height: Self.size.height)
        .rotationEffect(.degrees(pose.leanAngle), anchor: Self.hang)
        .offset(pose.lean)
        .offset(y: pose.float)
        .rotationEffect(.degrees(pose.swing), anchor: Self.hang)
    }

    private func art(_ image: UIImage) -> some View {
        Image(uiImage: image)
            .resizable()
            .interpolation(.high)
            .frame(width: Self.size.width, height: Self.size.height)
    }
}

private enum Art {
    static let body = UIImage(named: "body") ?? UIImage()
    static let eyeL = UIImage(named: "eye_L") ?? UIImage()
    static let eyeR = UIImage(named: "eye_R") ?? UIImage()
    private static let mouths = Dictionary(uniqueKeysWithValues: Expression.Mouth.allCases.filter { $0 != .none }.map {
        ($0, UIImage(named: "mouth_\($0.rawValue)") ?? UIImage())
    })

    static func mouth(_ mouth: Expression.Mouth) -> UIImage { mouths[mouth] ?? UIImage() }
}

/// One face per app state, using the expression layers. Looks are canvas pixels.
struct Expression {
    /// `none` is the bare body: no mouth layer at all.
    enum Mouth: String, CaseIterable { case smile, line, frown, o, none }
    enum Wink { case none, left, right }

    let mouth: Mouth
    let look: CGPoint
    var wink: Wink = .none

    static func of(_ face: AppModel.Face) -> Expression {
        switch face {
        case .welcome: return Expression(mouth: .smile, look: CGPoint(x: 0, y: -14))
        case .pair: return Expression(mouth: .line, look: CGPoint(x: -36, y: 0))
        case .paired: return Expression(mouth: .smile, look: CGPoint(x: 6, y: -6), wink: .left)
        case .idle: return Expression(mouth: .smile, look: .zero)
        case .offline: return Expression(mouth: .none, look: CGPoint(x: 0, y: 10))
        case .ring: return Expression(mouth: .o, look: CGPoint(x: 0, y: -22))
        case .listening: return Expression(mouth: .line, look: CGPoint(x: 0, y: -8))
        case .finishing: return Expression(mouth: .smile, look: CGPoint(x: 14, y: 4))
        case .review: return Expression(mouth: .smile, look: CGPoint(x: -8, y: -4))
        case .editing: return Expression(mouth: .line, look: CGPoint(x: 30, y: 6))
        case .sending: return Expression(mouth: .o, look: CGPoint(x: 34, y: -18))
        case .sent: return Expression(mouth: .smile, look: CGPoint(x: -6, y: -4), wink: .right)
        case .failed: return Expression(mouth: .frown, look: CGPoint(x: 0, y: 12))
        }
    }

    /// Both eyes share one offset, so it must fit both eyes' travel; below +12 an eye starts to cover the mouth.
    static func clamp(_ p: CGPoint) -> CGPoint {
        CGPoint(x: min(max(p.x, -41), 40), y: min(max(p.y, -42), 12))
    }
}

/// Per-frame character motion: gaze wander, blinks, float and the ring swing.
final class CharacterLife {
    struct Pose {
        var lidL: CGFloat = 1
        var lidR: CGFloat = 1
        var mouth: Expression.Mouth = .smile
        var eyes: CGSize = .zero
        /// How far each eye moves inward, past `eyes`: the screen-left eye right, the screen-right eye left.
        var cross: CGFloat = 0
        var lean: CGSize = .zero
        var leanAngle: Double = 0
        var float: CGFloat = 0
        var swing: Double = 0
    }

    /// Canvas pixels to points at 300 pt wide.
    private static let k = 300.0 / 1179
    /// Ring swing keyframes over 3.2 s: (fraction, degrees).
    private static let swingKeys: [(Double, Double)] = [
        (0, 0), (0.06, 11), (0.15, -9), (0.24, 6), (0.33, -4), (0.42, 2), (0.50, -1), (0.56, 0), (1, 0),
    ]
    /// The review face's loop, all on the smile: a look to screen-left, one to screen-right, then cross-eyed.
    /// (seconds, look, cross) in canvas pixels, eased linearly; the last key is the first pose again.
    private static let reviewKeys: [(Double, CGPoint, CGFloat)] = [
        (0, CGPoint(x: -8, y: -4), 0), (0.90, CGPoint(x: -8, y: -4), 0),
        (1.05, CGPoint(x: 8, y: -4), 0), (1.95, CGPoint(x: 8, y: -4), 0),
        (2.15, CGPoint(x: 0, y: -16), 34), (3.25, CGPoint(x: 0, y: -16), 34),
        (3.60, CGPoint(x: -8, y: -4), 0),
    ]

    private var face: AppModel.Face?
    private var faceSince: TimeInterval = 0
    private var last: TimeInterval = 0
    private var gaze = CGPoint.zero
    private var look = CGPoint.zero
    private var cross: CGFloat = 0
    private var aim = CGPoint.zero
    private var wanderAt: TimeInterval = 0
    private var nextBlink: TimeInterval = 0
    private var blinkUntil: TimeInterval = 0
    private var float: CGFloat = 0
    private var swing: Double = 0

    static func restingLid(_ face: AppModel.Face) -> CGFloat {
        switch face {
        // Half-closed reads as thinking.
        case .pair, .offline, .sending, .finishing: return 0.35
        default: return 1
        }
    }

    static func still(_ face: AppModel.Face) -> Pose {
        let expression = Expression.of(face)
        let look = Expression.clamp(expression.look)
        return Pose(lidL: restingLid(face), lidR: restingLid(face), mouth: expression.mouth,
                    eyes: CGSize(width: look.x * k, height: look.y * k))
    }

    func pose(at date: Date, face newFace: AppModel.Face) -> Pose {
        let t = date.timeIntervalSinceReferenceDate
        if newFace != face {
            face = newFace
            faceSince = t
        }
        if nextBlink == 0 { nextBlink = t + 2.6 }
        let dt = last == 0 ? 1.0 / 60 : min(max(t - last, 0), 0.1)
        last = t
        let inFace = t - faceSince

        switch newFace {
        case .listening, .review:
            aim = .zero
        case .ring where inFace < 0.5:
            aim = CGPoint(x: 0, y: -1)
        case .ring where inFace < 1.5:
            aim = .zero
            wanderAt = t + 1.5
        default:
            if t > wanderAt {
                // A deliberate look only drifts a little, so it still reads as looking there.
                let reach = Expression.of(newFace).look == .zero ? 1.0 : 0.35
                aim = CGPoint(x: .random(in: -0.7...0.7) * reach, y: .random(in: -0.5...0.5) * reach)
                wanderAt = t + 1.4 + .random(in: 0...2.4)
            }
        }
        let expression = Expression.of(newFace)
        let beat = newFace == .review ? Self.reviewBeat(inFace) : (look: expression.look, cross: 0)
        let follow = 1 - pow(0.91, dt * 60)
        gaze.x += (aim.x - gaze.x) * follow
        gaze.y += (aim.y - gaze.y) * follow
        if newFace == .review && inFace > 0.4 {
            // Once the eyes have arrived, the loop keeps its own timing rather than the follow's lag.
            look = beat.look
            cross = beat.cross
        } else {
            look.x += (beat.look.x - look.x) * follow
            look.y += (beat.look.y - look.y) * follow
            cross += (beat.cross - cross) * follow
        }
        let eyes = Expression.clamp(CGPoint(x: look.x + gaze.x * 18, y: look.y + gaze.y * (gaze.y < 0 ? 12 : 10)))

        if t > nextBlink {
            blinkUntil = t + 0.12
            nextBlink = t + (Double.random(in: 0..<1) < 0.2 ? 0.26 : 2.4 + .random(in: 0...3.6))
        }

        let period: Double?
        switch newFace {
        case .listening: period = 2.2
        case .idle, .sent, .welcome, .paired: period = 4.2
        default: period = nil
        }
        let floatTarget = period.map { -7 * (1 - cos(2 * .pi * t / $0)) / 2 } ?? 0
        let ease = 1 - pow(0.85, dt * 60)
        float += (floatTarget - float) * ease
        if newFace == .ring {
            swing = Self.swingAngle(inFace)
        } else {
            swing += (0 - swing) * ease
        }

        let lean = (newFace == .pair || newFace == .offline) ? 0.125 : 0.5
        let lid = t < blinkUntil ? 0.08 : Self.restingLid(newFace)
        // One wink, shortly after the face arrives; the other eye stays as it is.
        let winking = inFace > 0.35 && inFace < 0.85
        return Pose(
            lidL: winking && expression.wink == .left ? 0.08 : lid,
            lidR: winking && expression.wink == .right ? 0.08 : lid,
            mouth: expression.mouth,
            eyes: CGSize(width: eyes.x * Self.k, height: eyes.y * Self.k),
            cross: cross * Self.k,
            lean: CGSize(width: gaze.x * 7 * lean, height: gaze.y * 5 * lean),
            leanAngle: gaze.x * 3 * lean,
            float: float,
            swing: swing
        )
    }

    private static func swingAngle(_ elapsed: Double) -> Double {
        let p = elapsed.truncatingRemainder(dividingBy: 3.2) / 3.2
        for i in 1..<swingKeys.count where p <= swingKeys[i].0 {
            let (p0, a0) = swingKeys[i - 1]
            let (p1, a1) = swingKeys[i]
            let u = (p - p0) / (p1 - p0)
            return a0 + (a1 - a0) * u * u * (3 - 2 * u)
        }
        return 0
    }

    private static func reviewBeat(_ elapsed: Double) -> (look: CGPoint, cross: CGFloat) {
        let s = elapsed.truncatingRemainder(dividingBy: reviewKeys[reviewKeys.count - 1].0)
        for i in 1..<reviewKeys.count where s <= reviewKeys[i].0 {
            let (s0, l0, c0) = reviewKeys[i - 1]
            let (s1, l1, c1) = reviewKeys[i]
            let u = CGFloat((s - s0) / (s1 - s0))
            return (CGPoint(x: l0.x + (l1.x - l0.x) * u, y: l0.y + (l1.y - l0.y) * u), c0 + (c1 - c0) * u)
        }
        return (reviewKeys[0].1, reviewKeys[0].2)
    }
}
