# Build Letterbox Desk

## Build

The Mac listener needs Python 3.11 or newer. Its native window uses SwiftUI and AppKit and
is compiled with the installed Xcode Command Line Tools, not a Mac Xcode project. Its explicit
macOS 13.0 deployment target is compile-verified; runtime testing here is on macOS 27.
Older supported systems still need runtime validation. Agent Bell explicitly targets iOS 15.0 or newer and needs Xcode to build.

From the repository root, create a development environment:

```sh
cd mac
uv venv --python '>=3.11' .venv
uv pip install --python .venv/bin/python -e .
cd ..
mac/Tools/build-window.sh
```

Without uv, use `python3 -m venv mac/.venv` with Python 3.11+, then
`mac/.venv/bin/python -m pip install -e mac`.

The Mac output is `mac/build/Letterbox Desk.app`, containing `Contents/MacOS/bell-pair` and
bundled artwork. The builder ad-hoc signs and verifies it before replacing the previous
output. This builds only the window; it does not install a login item, run the listener,
configure a picture store or send probes. An optional absolute `.app` output path is accepted.
Ad-hoc signing is for local builds; it is not notarization or a public distribution package.
The listener reaches this bundle from its source checkout, so keep that checkout in place.

`ios/project.yml` is the iOS project source of truth. After editing it:

```sh
cd ios
xcodegen generate
xcodebuild -project BellDesk.xcodeproj -scheme BellDesk -configuration Debug \
  -destination 'generic/platform=iOS Simulator' -derivedDataPath build/simulator \
  CODE_SIGNING_ALLOWED=NO build
cd ..
```

Commit the regenerated `ios/BellDesk.xcodeproj/project.pbxproj` and any generated Info.plist
changes with the YAML. Do not make independent edits to the generated project. XcodeGen is
needed for regeneration; a clean checkout can build its committed project without regenerating.

For a signed device build/install, connect and trust the iPhone, configure your Apple team,
and deliberately run `ios/install-to-phone.sh YOUR_TEAM_ID`. It requires `ideviceinstaller`
(`brew install ideviceinstaller` with Homebrew). This command installs on the connected phone;
it is not part of the checks below. Open Agent Bell and pair after installation.

For the full validation commands and release gate, see [TEST.md](TEST.md).
To run the bare `bell-desk` command used in the guide, first activate the environment with
`source mac/.venv/bin/activate`, or use `mac/.venv/bin/bell-desk` from the repository root.
