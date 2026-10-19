/// Joins what the speech recogniser reports into one text.
///
/// When the speaker pauses, iOS closes the utterance: that result carries speech metadata and real
/// segment times, measured from the start of the audio, and the results after it start again from
/// the first new word. Closed utterances are kept in order. A close that starts before kept ones
/// ended re-reports that stretch of audio, so it replaces all of them instead of being added.
struct Transcript {
    private var kept: [(text: String, end: Double)] = []
    private var open = ""  // the utterance still being spoken, from partial results

    var text: String { (kept.map(\.text) + [open]).filter { !$0.isEmpty }.joined(separator: " ") }

    /// `start` and `end` are the result's first segment start and last segment end, in seconds (0 when unknown).
    mutating func add(_ newText: String, closesUtterance: Bool, start: Double, end: Double) {
        // An empty result never erases what was heard.
        guard !newText.isEmpty else { return }
        guard closesUtterance else {
            open = newText
            return
        }
        if end > 0 {
            while let last = kept.last, last.end > start { kept.removeLast() }
            kept.append((newText, end))
        } else if newText != kept.last?.text {
            // Without times, only an identical text can be told apart as a repeat; it keeps the known times.
            kept.append((newText, end))
        }
        open = ""
    }
}
