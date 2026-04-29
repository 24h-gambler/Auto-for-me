#!/usr/bin/env bash
# Auto-for-me — Start Chrome + bot in one command (macOS / Linux)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "[1/2] Launching Chrome (port 9222)..."
bash "$ROOT/scripts/launch_chrome.sh"

echo
echo "[2/2] Starting bot..."
if [[ ! -f ".venv/bin/activate" ]]; then
  echo ".venv not found. Setup first:"
  echo "  python3 -m venv .venv"
  echo "  source .venv/bin/activate"
  echo "  pip install -r requirements.txt"
  echo "  playwright install chromium"
  exit 1
fi

# macOS: open new Terminal window with bot. Linux: just run in foreground.
if [[ "$(uname -s)" == "Darwin" ]]; then
  osascript <<EOF
tell application "Terminal"
  activate
  do script "cd '$ROOT' && source .venv/bin/activate && python -m src.main"
end tell
EOF
  echo "Bot started in a new Terminal window."
else
  source .venv/bin/activate
  exec python -m src.main
fi

echo
echo "Done. Now:"
echo "  1. In the Chrome window: log in to Korail and search trains"
echo "  2. In Telegram: /refresh -> click a button"
