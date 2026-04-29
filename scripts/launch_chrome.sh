#!/usr/bin/env bash
# Auto-for-me — Launch real Chrome with remote debugging (macOS / Linux)
#
# Usage:
#   bash scripts/launch_chrome.sh
#   bash scripts/launch_chrome.sh --use-default-profile
#
# After it's running, set CHROME_CDP_URL=http://127.0.0.1:9222 in .env

set -euo pipefail

PORT=9222
USE_DEFAULT=0
for arg in "$@"; do
  case "$arg" in
    --use-default-profile) USE_DEFAULT=1 ;;
    --port=*) PORT="${arg#*=}" ;;
  esac
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# 1) Find Chrome
CHROME=""
case "$(uname -s)" in
  Darwin)
    candidates=(
      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
      "$HOME/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    )
    ;;
  Linux)
    candidates=(
      "/usr/bin/google-chrome"
      "/usr/bin/google-chrome-stable"
      "/usr/bin/chromium"
      "/usr/bin/chromium-browser"
    )
    ;;
  *)
    echo "Unsupported OS: $(uname -s)"
    exit 1
    ;;
esac
for c in "${candidates[@]}"; do
  if [[ -x "$c" ]]; then
    CHROME="$c"
    break
  fi
done
if [[ -z "$CHROME" ]]; then
  echo "ERROR: Google Chrome not found. Install from https://www.google.com/chrome/"
  exit 1
fi
echo "OK Chrome: $CHROME"

# 2) Profile dir
if [[ "$USE_DEFAULT" == "1" ]]; then
  case "$(uname -s)" in
    Darwin)
      PROFILE_DIR="$HOME/Library/Application Support/Google/Chrome"
      ;;
    Linux)
      PROFILE_DIR="$HOME/.config/google-chrome"
      ;;
  esac
  echo "Using DEFAULT Chrome profile (close all Chrome windows first)."
  if pgrep -x "Google Chrome" >/dev/null 2>&1 || pgrep -x "chrome" >/dev/null 2>&1; then
    echo "ERROR: Chrome is running. Quit all Chrome windows first, or omit --use-default-profile."
    exit 1
  fi
else
  PROFILE_DIR="$ROOT/state/chrome-profile"
  mkdir -p "$PROFILE_DIR"
  echo "Using SEPARATE bot profile: $PROFILE_DIR"
fi

# 3) Check port
if (echo > /dev/tcp/127.0.0.1/$PORT) >/dev/null 2>&1; then
  echo "Port $PORT already in use. Chrome probably running with debugging."
  echo "Add to .env :  CHROME_CDP_URL=http://127.0.0.1:$PORT"
  exit 0
fi

# 4) Launch (background, detached)
nohup "$CHROME" \
  --remote-debugging-port="$PORT" \
  --user-data-dir="$PROFILE_DIR" \
  --no-first-run \
  --no-default-browser-check \
  --lang=ko-KR \
  "https://www.korail.com/ticket/login" \
  >/dev/null 2>&1 &
disown

echo ""
echo "OK Chrome launched on port $PORT."
echo "Next:"
echo "  1. In the launched Chrome: log in to Korail."
echo "  2. Go to https://www.korail.com/ticket/search/general and search."
echo "  3. Add to .env :  CHROME_CDP_URL=http://127.0.0.1:$PORT"
echo "  4. Run: python -m src.main"
echo "  5. In Telegram: /refresh"
