#!/bin/sh
# Compiles the app's Transcript.swift for macOS and checks it against recogniser result sequences.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
ios=$(dirname "$here")
out=${TMPDIR:-/tmp}/belldesk-transcriptcheck
swiftc -O -o "$out" \
    "$ios/BellDesk/Speech/Transcript.swift" \
    "$here/TranscriptCheck/main.swift"
"$out"
