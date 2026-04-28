"""
셋업 점검 스크립트 — 실제 봇을 띄우기 전에 .env 가 제대로 채워졌고
자격증명이 동작하는지 확인합니다.

  python scripts/check_setup.py

성공: exit 0
실패: exit 1 + 수정 가이드
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_env() -> dict:
    """Minimal .env parser — config 모듈을 import 하기 전에 동작해야 하므로 직접 파싱."""
    env: dict = {}
    p = ROOT / ".env"
    if not p.exists():
        return env
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        # 인라인 주석 제거 + 따옴표 제거
        v = v.split("#")[0].strip().strip('"').strip("'")
        env[k.strip()] = v
    return env


async def check_telegram(token: str, chat_id: str) -> tuple[bool, str]:
    try:
        import httpx
    except ImportError:
        return False, "httpx 미설치 → pip install -r requirements.txt"

    if not token:
        return False, "TELEGRAM_BOT_TOKEN 비어있음 — @BotFather 에서 발급 후 .env 에 입력"
    if "<" in token or ">" in token:
        return False, "TELEGRAM_BOT_TOKEN 에 꺾쇠(<>)가 들어있음 — 순수 토큰만 입력"

    async with httpx.AsyncClient(timeout=10) as c:
        try:
            r = await c.get(f"https://api.telegram.org/bot{token}/getMe")
        except Exception as exc:  # noqa: BLE001
            return False, f"네트워크 오류: {exc}"

        if r.status_code != 200:
            return False, f"getMe HTTP {r.status_code} — 토큰 형식이 잘못됐을 수 있음"
        data = r.json()
        if not data.get("ok"):
            return False, f"토큰 거부됨: {data}"
        bot_name = data["result"].get("username", "?")

        if not chat_id:
            return True, f"@{bot_name} 토큰 OK (chat_id 미설정)"

        try:
            r = await c.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": int(chat_id), "text": "✅ Auto-for-me 셋업 점검 OK"},
            )
        except Exception as exc:  # noqa: BLE001
            return False, f"sendMessage 실패: {exc}"

        if r.status_code != 200 or not r.json().get("ok"):
            return False, (
                f"chat_id={chat_id} 로 메시지 못 보냄. "
                f"봇에게 텔레그램에서 /start 한번 보내신 다음 다시 시도하세요. "
                f"({r.text[:200]})"
            )
    return True, f"@{bot_name} + chat_id={chat_id} 메시지 도달 OK"


def check_korail(env: dict) -> tuple[bool, str]:
    if not (env.get("KORAIL_ID") and env.get("KORAIL_PW")):
        return False, "KORAIL_ID / KORAIL_PW 비어있음 — letskorail.com 계정 정보 입력"
    return True, f"KORAIL_ID={env['KORAIL_ID'][:3]}*** 설정됨 (실제 로그인은 봇 가동 시 검증)"


def check_playwright() -> tuple[bool, str]:
    try:
        import playwright  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, f"playwright 미설치 ({exc}) → pip install -r requirements.txt"
    cache = Path.home() / ".cache" / "ms-playwright"
    if not cache.exists() or not any(cache.glob("chromium-*")):
        return False, "chromium 미설치 → playwright install chromium"
    return True, "playwright + chromium 준비됨"


async def main() -> int:
    env = _load_env()
    if not env:
        print("❌ .env 파일이 없습니다. cp .env.example .env 후 값을 채워주세요.")
        return 1

    fails = 0
    print("=" * 50)
    print("Auto-for-me 셋업 점검")
    print("=" * 50)

    print("1) ANTHROPIC_API_KEY ......", end=" ")
    if env.get("ANTHROPIC_API_KEY"):
        print("✅ 설정됨 (자유채팅/쿠팡 가능)")
    else:
        print("⚪ 비어있음 (KTX 슬래시 명령에는 불필요)")

    print("2) Telegram ...............", end=" ", flush=True)
    ok, msg = await check_telegram(
        env.get("TELEGRAM_BOT_TOKEN", ""),
        env.get("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",")[0].strip(),
    )
    print(("✅ " if ok else "❌ ") + msg)
    if not ok:
        fails += 1

    print("3) Korail .................", end=" ")
    ok, msg = check_korail(env)
    print(("✅ " if ok else "❌ ") + msg)
    if not ok:
        fails += 1

    print("4) Playwright .............", end=" ")
    ok, msg = check_playwright()
    print(("✅ " if ok else "❌ ") + msg)
    if not ok:
        fails += 1

    print("=" * 50)
    if fails:
        print(f"❌ {fails}건 실패. 위 메시지대로 수정 후 다시 실행하세요.")
        return 1

    print("✅ 전부 OK!")
    print()
    print("다음 단계:")
    print("  1. python -m src.main         ← 봇 가동")
    print("  2. 텔레그램에서 /start        ← 봇 활성화 확인")
    print("  3. /book 서울 부산 2026-05-10 09:00 120   ← 실제 예매")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
