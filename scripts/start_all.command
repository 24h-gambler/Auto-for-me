#!/bin/bash
# Auto-for-me — macOS double-clickable launcher.
#
# Finder 에서 더블클릭하면 Terminal 이 열리고 Chrome + 봇 자동 시작.
# Desktop 에 alias 만들어 두면 PC 의 .bat 더블클릭과 동일한 경험.
#
# 처음 더블클릭 시 macOS Gatekeeper 경고가 뜨면:
#   우클릭 → "열기" → "열기" 한 번만 누르면 다음부턴 그냥 더블클릭으로 실행됨.

set -e

# 1) 프로젝트 위치 자동 탐색 (alias 로 옮겨도 동작하도록)
SCRIPT_PATH="${BASH_SOURCE[0]}"
# alias 의 경우 readlink 로 원본 경로 풀기
while [[ -L "$SCRIPT_PATH" ]]; do
  SCRIPT_PATH="$(readlink "$SCRIPT_PATH")"
done
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"

# 자기 위치 → 한 단계 위가 프로젝트 루트
PROJECT="$(cd "$SCRIPT_DIR/.." && pwd)"
if [[ ! -f "$PROJECT/scripts/launch_chrome.sh" ]]; then
  # 일반적인 위치 후보
  for candidate in "$HOME/Auto-for-me" "$HOME/auto-for-me" "$HOME/Documents/Auto-for-me"; do
    if [[ -f "$candidate/scripts/launch_chrome.sh" ]]; then
      PROJECT="$candidate"
      break
    fi
  done
fi
if [[ ! -f "$PROJECT/scripts/launch_chrome.sh" ]]; then
  echo "ERROR: Auto-for-me 프로젝트를 못 찾음."
  echo "  검색한 곳: $SCRIPT_DIR/..  /  ~/Auto-for-me  /  ~/auto-for-me"
  echo "  팁: 이 파일을 복사하지 말고 Finder 에서 alias (option+drag) 로 Desktop 에 두세요."
  read -p "엔터 누르면 닫힘..." -n 1
  exit 1
fi

cd "$PROJECT"
echo "=========================================="
echo " Auto-for-me — starting"
echo " Project: $PROJECT"
echo "=========================================="
echo

# 2) Chrome 띄우기
echo "[1/2] Chrome (포트 9222) 띄우는 중..."
bash "$PROJECT/scripts/launch_chrome.sh"
echo

# 3) 봇 가동
echo "[2/2] 봇 시작 — 이 Terminal 창에서 동작합니다."
echo "      (창 닫으면 봇 꺼짐. 멈추려면 Ctrl+C)"
echo
if [[ ! -f "$PROJECT/.venv/bin/activate" ]]; then
  echo "ERROR: .venv 없음. 먼저:"
  echo "  python3 -m venv .venv"
  echo "  source .venv/bin/activate"
  echo "  pip install -r requirements.txt"
  echo "  playwright install chromium"
  read -p "엔터 누르면 닫힘..." -n 1
  exit 1
fi

source "$PROJECT/.venv/bin/activate"
exec python -m src.main
