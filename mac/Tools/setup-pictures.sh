#!/bin/bash
# One picture prompt shared by the installer and its isolated shell tests.
set -euo pipefail
bell=${1:?bell-desk executable required}
if [ ! -t 0 ]; then
  echo "  Set up pictures later with: bell-desk attachments setup"
  exit 0
fi
read -r -p "  Set up pictures for your agents? [Y/n] " answer || answer=n
case "${answer:-y}" in
  [Yy]*) "$bell" attachments setup ;;
  *) echo "  Set up pictures later with: bell-desk attachments setup" ;;
esac
