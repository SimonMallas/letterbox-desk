import AudioToolbox
import AVFoundation

/// The ring sound. Drop `chime.wav` into BellDesk/Resources and rebuild; until then short system sounds play.
/// A letter for the person rings differently from team mail: the chime twice, or a different system sound.
@MainActor
enum Chime {
    private static var players: [AVAudioPlayer] = []

    private static var fileURL: URL? {
        Bundle.main.url(forResource: "chime", withExtension: "wav")
    }

    static func play(forYou: Bool) {
        guard let url = fileURL else {
            AudioServicesPlaySystemSound(forYou ? 1007 : 1000)
            return
        }
        let session = AVAudioSession.sharedInstance()
        try? session.setCategory(.playback, options: .mixWithOthers)
        try? session.setActive(true)
        players = (forYou ? [0, 0.35] : [0]).compactMap { delay in
            guard let player = try? AVAudioPlayer(contentsOf: url) else { return nil }
            player.prepareToPlay()
            player.play(atTime: player.deviceCurrentTime + delay)
            return player
        }
    }
}
