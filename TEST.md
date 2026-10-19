# Test Letterbox Desk

Set up the development environment using [BUILD.md](BUILD.md) first. Run the commands below
from the repository root unless a command explicitly changes directory.

## Automated checks

Run Python tests **from `mac/`** so they use this checkout:

```sh
cd mac
.venv/bin/python -m unittest tests.test_listener tests.test_desk -v
cd ..
mac/Tools/check-desk-model.sh
ios/Tools/check-vectors.sh
ios/Tools/check-transcript.sh
```

The Python tests create scratch state, mailboxes and helpers. The native checker exercises
real text views and model behavior with simulated Dictation-menu actions; it does not start
OS speech recognition. The iOS checks compile shared sources for macOS: one pins crypto,
event parsing and ring-policy behavior, the other checks transcript assembly. They do not
replace an iOS app build or a live Dictation test.

Optional integration checks skip unless explicitly configured:

- `BELL_DESK_TEST_PUBLIC_HELPER`: an absolute public `letterbox` executable for setup/send tests.
- `BELL_DESK_TEST_CMUX_REPO`, `BELL_DESK_TEST_TMUX_REPO`, `BELL_DESK_TEST_ZELLIJ_REPO`, and
  `BELL_DESK_TEST_HERDR_REPO`: absolute local edition checkouts, all four required. Tests archive
  their existing `origin/main` refs into scratch directories; they neither fetch nor modify the
  checkouts. Keep those refs current deliberately. No personal Developer paths are assumed.

Do not treat skipped integrations as verified support for a different helper version.

## Done gate

Before a release candidate is accepted:

1. Python suite passes; report the count and any skipped optional integrations.
2. Native model/editor checks, iOS vectors and transcript checks pass.
3. Mac bundle builds and passes `codesign --verify --strict`; its Info.plist has
   `dev.aitortoise.letterboxdesk` and no `LSUIElement` key.
4. The iOS project regenerates reproducibly, an unsigned Simulator build passes, and its
   bundle ID is `dev.aitortoise.agentbell`.
5. `bash -n install-mac.sh ios/install-to-phone.sh mac/Tools/build-window.sh
   mac/Tools/setup-pictures.sh` and `git diff --check` pass.
6. Inspect Day/Night fixture captures and review changed user flows. With permission to run a
   live deployment, check pairing, connection/ringing, typed sends and per-copy retry warnings,
   Mac Dictation start/stop and provisional text, screenshot paste/drop, conversation scrolling,
   and picture setup with an actual recipient read-proof. Fixture tests are not live evidence.
7. Check the artwork exceptions in LICENSE; forks must replace the reserved character art and
   icons. Build outputs, private state, credentials, signing material and personal screenshots
   stay out of the repository.

## Continuous integration

The macOS Actions workflow uses `macos-15`, Python 3.13.7, and Xcode 16.4's Swift 6.1.2, and
asserts those language versions before building. Actions themselves are pinned by commit SHA.
It runs the Mac suite from `mac/`, an ad-hoc-signed Mac bundle build under `RUNNER_TEMP`, and
the two device-free iOS check scripts. Optional edition integrations skip unless supplied.
No device install, listener or live probe is part of CI. A missing or different pinned
toolchain fails rather than silently selecting another compiler. See the official
[macOS runner image inventory](https://github.com/actions/runner-images/blob/main/images/macos/macos-15-Readme.md).
