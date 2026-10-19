#!/bin/bash
# Build Bell and install it on the iPhone plugged into this Mac.
# Usage: ios/install-to-phone.sh YOUR_TEAM_ID
set -euo pipefail

team="${1:?usage: ios/install-to-phone.sh YOUR_TEAM_ID}"
cd "$(dirname "$0")"

if ! command -v ideviceinstaller >/dev/null; then
  echo "ideviceinstaller is required. With Homebrew: brew install ideviceinstaller" >&2
  exit 1
fi

echo "▸ Building Bell for your iPhone"
xcodebuild -project BellDesk.xcodeproj -scheme BellDesk -configuration Debug \
  -destination 'generic/platform=iOS' -allowProvisioningUpdates \
  DEVELOPMENT_TEAM="$team" -derivedDataPath build/device -quiet build

rm -rf build/ipa && mkdir -p build/ipa/Payload
cp -R build/device/Build/Products/Debug-iphoneos/BellDesk.app build/ipa/Payload/
(cd build/ipa && zip -qr BellDesk.ipa Payload)

echo "▸ Installing over the cable"
ideviceinstaller install build/ipa/BellDesk.ipa
echo "Installed. Open Bell on the phone."
