#!/bin/sh
# Compiles the app's own crypto and event-parsing sources for macOS and checks them against mac/tests/vectors.json.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
ios=$(dirname "$here")
out=${TMPDIR:-/tmp}/belldesk-vectorcheck
swiftc -O -o "$out" \
    "$ios/BellDesk/Crypto/BellCrypto.swift" \
    "$ios/BellDesk/Net/EventParser.swift" \
    "$here/VectorCheck/main.swift"
"$out" "$ios/../mac/tests/vectors.json"
