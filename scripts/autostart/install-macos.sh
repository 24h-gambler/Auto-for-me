#!/usr/bin/env bash
# macOS LaunchAgent — runs the bot whenever you log in. Survives reboots.
#
#   bash scripts/autostart/install-macos.sh
#
# To remove:
#   launchctl unload ~/Library/LaunchAgents/com.autoforme.bot.plist
#   rm ~/Library/LaunchAgents/com.autoforme.bot.plist

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$ROOT/.venv/bin/python"
PLIST="$HOME/Library/LaunchAgents/com.autoforme.bot.plist"
mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/state/logs"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.autoforme.bot</string>
  <key>ProgramArguments</key><array>
    <string>$PY</string><string>-m</string><string>src.main</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$ROOT/state/logs/stdout.log</string>
  <key>StandardErrorPath</key><string>$ROOT/state/logs/stderr.log</string>
</dict></plist>
EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "Installed: $PLIST"
echo "Logs: $ROOT/state/logs/"
