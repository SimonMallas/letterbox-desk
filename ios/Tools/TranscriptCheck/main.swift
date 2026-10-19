// Checks the app's Transcript.swift against recogniser result sequences.
// Built and run by Tools/check-transcript.sh.

import Foundation

/// One recogniser result: its text, whether it closes an utterance, and its segment times in seconds.
typealias Result = (text: String, closes: Bool, start: Double, end: Double)
func partial(_ text: String) -> Result { (text, false, 0, 0) }
func close(_ text: String, _ start: Double, _ end: Double) -> Result { (text, true, start, end) }

var failures = 0
func check(_ name: String, _ results: [Result], _ want: String) {
    var t = Transcript()
    for r in results { t.add(r.text, closesUtterance: r.closes, start: r.start, end: r.end) }
    if t.text == want {
        print("OK    \(name)")
    } else {
        failures += 1
        print("FAIL  \(name): got \"\(t.text)\", want \"\(want)\"")
    }
}

// The sequence logged on the iPhone X (iOS 16.7, on device) on 19 Oct: partial results carry no
// times; a pause closes the first sentence with real times, then the results start again.
check("pause keeps both sentences", [
    partial("Now"), partial("Now this is"), partial("Now this is the"), partial("Now this is the first"),
    partial("Now this is the first sentence."), close("Now this is the first sentence.", 0.68, 4.14),
    partial("And"), partial("And now"), partial("And now this"), partial("And now this"),
    partial("And now this is"), partial("And now this is the"), partial("And now this is the second"),
    partial("And now this is the second sentence."), close("And now this is the second sentence.", 9.50, 12.95),
], "Now this is the first sentence. And now this is the second sentence.")

check("no pause", [partial("Send"), partial("Send it"), partial("Send it to Alice."), close("Send it to Alice.", 0.4, 1.9)],
      "Send it to Alice.")
check("three sentences", [close("One.", 0.3, 0.7), partial("Two"), close("Two.", 2.1, 2.5), partial("Three"), close("Three.", 4.0, 4.6)],
      "One. Two. Three.")
check("let go after a pause: the repeated final is kept once", [partial("All done."), close("All done.", 0.5, 1.4), close("All done.", 0.5, 1.4)],
      "All done.")
check("let go after a pause: an empty final keeps the sentence", [partial("All done."), close("All done.", 0.5, 1.4), close("", 0, 0)],
      "All done.")
check("an empty close does not erase the open sentence", [partial("Hello world."), close("", 0, 0)],
      "Hello world.")
check("let go mid-sentence after a pause: the open words are kept", [close("One.", 0.2, 0.6), partial("Two")],
      "One. Two")
check("next sentence opens with the same words", [close("I think so.", 0.5, 1.4), partial("I think not"), close("I think not.", 3.0, 4.0)],
      "I think so. I think not.")
check("a revised close replaces the sentence", [close("Hello world.", 0.4, 1.2), partial("Hello, world."), close("Hello, world.", 0.4, 1.3)],
      "Hello, world.")
check("an extended close replaces the sentence", [close("Yes", 0.4, 0.7), partial("Yes please"), close("Yes please.", 0.4, 1.1)],
      "Yes please.")
check("the same word said again later is kept", [close("Yes", 0.4, 0.7), partial("Yes please"), close("Yes please.", 2.5, 3.2)],
      "Yes Yes please.")
check("a close spanning two kept sentences replaces both", [close("One.", 0.2, 1), close("Two.", 2, 3), close("One. Two!", 0.2, 3.1)],
      "One. Two!")
check("closes without times: a repeat is kept once", [close("All done.", 0, 0), close("All done.", 0, 0)],
      "All done.")
check("a repeat without times keeps the known times", [close("All done.", 0.5, 1.4), close("All done.", 0, 0), close("All done.", 0.5, 1.4)],
      "All done.")
check("closes without times: different sentences are both kept", [close("One.", 0, 0), close("Two.", 0, 0)],
      "One. Two.")
check("nothing heard", [], "")

if failures > 0 {
    print("\(failures) failed")
    exit(1)
}
print("all passed")
