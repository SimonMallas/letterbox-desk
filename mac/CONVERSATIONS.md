# Conversations in Letterbox Desk

Opening Desk chooses the newest unread conversation, otherwise the most recently active one.
With no letters yet it shows Letterbox Desk and points to New letter. Choose an agent for one
rolling conversation, including letters sent from the phone. New letter
writes without a reply target. In a conversation, the selected agent receives a reply to its latest
letter; added agents receive new letters with the existing “Also addressed to” note. Earlier
conversation text is never forwarded. Each copy has its own result. Use Sent to retry an
unconfirmed copy after checking its warning; published copies are refused, including copies
already published by an earlier retry.

Done — keep files hides the current conversation locally. Show done restores it, and new mail
resurfaces it unread. Keep defaults to three days, minimum one, and hides only older read letters. Unread letters always
appear; opening an older unread letter keeps it visible until leaving that conversation. Files stay on disk.
Look chooses Day, Auto (Mac appearance), or Night. Ring Off keeps phones connected and dictation
available, while suppressing new mail ring events. There are no corner letter or send-result cards.

Pictures can be dropped or pasted with ⌘V directly into the editor; the chip keeps its Remove control. PNG/JPEG only,
10 MB maximum, 16 million pixels. `bell-desk attachments setup` creates a private external store
and asks every agent for a read proof; the listener verifies replies automatically. A blocked send
can ask one agent to set up pictures, or send just the text. See CONTRACT.md Part B. No live shared picture folder is created by the build.
The draft chip can be removed before sending. Stored pictures are inert; their path/type/size/hash
are a final line in each letter. Missing/changed stored copies are labelled in the conversation.

## Fixture preview (no listener or phone)

Build the app without installing or launching the listener:

```sh
mac/Tools/build-window.sh '/tmp/Letterbox Desk.app'
'/tmp/Letterbox Desk.app/Contents/MacOS/bell-pair' --demo conversation \
  --fixture mac/fixtures/conversations.json --look day --snapshot /tmp/desk-day.png
```

Repeat with night. The app bundles its Bell art. The fixture never reads a real bus or writes real
state; native AppKit controls are captured after layout. install-mac.sh builds the same bundle at
mac/build/Letterbox Desk.app; all Python window launches use its Contents/MacOS/bell-pair. The
bundle has an identifier and deliberately no LSUIElement; accessory mode is set in code. A failed
native build keeps the existing plain pairing-dialog fallback rather than using a stale executable.

The microphone activates Desk and makes its editor first responder, then invokes the native
Dictation menu item to start or stop. The stop indicator follows the menu state. If no item is
available, it falls back to a nil-target start action and shows “Use the Dictation shortcut”.
No Dictation selector is sent directly to NSTextView. The placeholder hides whenever native
text or marked text is present. Replies and New letter use the same native editor.

`mac/Tools/check-desk-model.sh` exercises opening selection and isolated native responder/paste
routing and marked-text preservation without connecting to a real bus or starting audio recognition.
