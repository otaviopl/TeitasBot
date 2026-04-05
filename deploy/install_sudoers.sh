#!/usr/bin/env bash
# Install the sudoers drop-in so the bot can restart itself without a password.
# Run once: sudo bash deploy/install_sudoers.sh

set -euo pipefail

SRC="$(dirname "$0")/sudoers/personal-assistant-bot"
DEST="/etc/sudoers.d/personal-assistant-bot"

if [ ! -f "$SRC" ]; then
  echo "Error: source file not found: $SRC"
  exit 1
fi

cp "$SRC" "$DEST"
chmod 440 "$DEST"
visudo -c -f "$DEST"

echo "Sudoers drop-in installed at $DEST"
