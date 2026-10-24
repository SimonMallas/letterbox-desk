#!/bin/sh
# Compiles the app's own crypto, event-parsing, listener-client and ring-policy sources for macOS and checks them.
# Values the Mac shares come from mac/tests/vectors.json.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
ios=$(dirname "$here")
out=${TMPDIR:-/tmp}/belldesk-vectorcheck
swiftc -O -o "$out" \
    "$ios/BellDesk/Crypto/BellCrypto.swift" \
    "$ios/BellDesk/Net/EventParser.swift" \
    "$ios/BellDesk/Net/ListenerClient.swift" \
    "$ios/BellDesk/Model/RingPolicy.swift" \
    "$here/VectorCheck/main.swift"
"$out" "$ios/../mac/tests/vectors.json"
