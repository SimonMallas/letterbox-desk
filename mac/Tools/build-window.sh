#!/bin/bash
# Build the native window bundle without installing or starting a listener.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
mac="$(dirname "$here")"
output="${1:-$mac/build/Letterbox Desk.app}"
case "$output" in /*.app) ;; *) echo 'Bundle output must be an absolute .app path' >&2; exit 2 ;; esac
command -v swiftc >/dev/null || { echo 'Swift is unavailable' >&2; exit 1; }
parent="$(dirname "$output")"
mkdir -p "$parent"
staging=$(mktemp -d "$parent/.desk-window.XXXXXX")
trap 'rm -rf "$staging"' EXIT
bundle="$staging/Letterbox Desk.app"
mkdir -p "$bundle/Contents/MacOS" "$bundle/Contents/Resources/Art"
cat > "$bundle/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>dev.aitortoise.letterboxdesk</string>
<key>CFBundleExecutable</key><string>bell-pair</string>
<key>CFBundleName</key><string>Letterbox Desk</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>LSMinimumSystemVersion</key><string>13.0</string>
</dict></plist>
PLIST
swiftc -target "$(uname -m)-apple-macos13.0" -O -parse-as-library "$mac"/pairwindow/*.swift -o "$bundle/Contents/MacOS/bell-pair"
cp "$mac"/assets/*.png "$bundle/Contents/Resources/Art/"
codesign --force --sign - "$bundle"
codesign --verify --strict "$bundle"
# Retain the previous generated bundle until the replacement is complete.
if [ -e "$output" ]; then
  previous="$staging/previous.app"
  mv "$output" "$previous"
  if ! mv "$bundle" "$output"; then mv "$previous" "$output"; exit 1; fi
else
  mv "$bundle" "$output"
fi
printf 'Built %s\n' "$output"
