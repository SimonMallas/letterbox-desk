#!/bin/sh
set -eu
here=$(cd "$(dirname "$0")" && pwd)
mac=$(dirname "$here")
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
set --
for source in "$mac"/pairwindow/*.swift; do
  case "$source" in */PairWindow.swift) ;; *) set -- "$@" "$source" ;; esac
done
swiftc -O -parse-as-library "$@" "$here/DeskModelCheck/Check.swift" -o "$scratch/check"
"$scratch/check"
