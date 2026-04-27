"""
Standalone KTX watcher — designed for GitHub Actions cron.

Reads `watch_list.json` from the repo (or any URL via WATCH_LIST_URL env var),
performs a single Playwright pass per watch entry, and pings Telegram if any
seat is available. **It never books — booking is the user's PC job.**

Usage (CI):
  - .github/workflows/ktx-watcher.yml runs this every N minutes.
  - Secrets needed: KORAIL_ID, KORAIL_PW, TELEGRAM_BOT_TOKEN.
  - watch_list.json structure:
        [
          {"id":"w-1","origin":"서울","destination":"부산",
           "date":"2026-05-10","time":"09:00","window_minutes":120,
           "owner_chat_id":12345678}
        ]

Local test:
  python scripts/watch_ktx.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

# Reuse selectors & DOM logic so we have a single source of truth.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.ktx import selectors as S  # noqa: E402

WATCH_LIST_PATH = Path("watch_list.json")
ALERT_COOLDOWN_SEC = 30 * 60  # don't spam: same watch alerts at most every 30 min
ALERT_STATE_PATH = Path(".watcher_state.json")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def _within_window(dep: str, target: str, window_min: int) -> bool:
    try:
        d = datetime.strptime(dep, "%H:%M")
        t = datetime.strptime(target, "%H:%M")
    except ValueError:
        return False
    return abs((d - t).total_seconds()) <= window_min * 60


async def _send_telegram(token: str, chat_id: int, text: str) -> None:
    if not token or not chat_id:
        print("[watcher] no telegram creds; skip notify")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.post(url, json={"chat_id": chat_id, "text": text})
            if r.status_code != 200:
                print(f"[watcher] telegram error {r.status_code}: {r.text[:200]}")
        except Exception as exc:  # noqa: BLE001
            print(f"[watcher] telegram fail: {exc}")


async def _login(page, ko_id: str, ko_pw: str) -> bool:
    await page.goto(S.HOME_URL, wait_until="domcontentloaded")
    if await page.locator("a:has-text('로그아웃')").count():
        return True
    await page.goto(S.LOGIN_URL, wait_until="domcontentloaded")
    await page.fill(S.ID_INPUT, ko_id)
    await page.fill(S.PW_INPUT, ko_pw)
    if await page.locator(S.CAPTCHA_IMG).count():
        return False  # captcha → skip this run, owner will be alerted
    await page.click(S.LOGIN_BTN)
    try:
        await page.wait_for_selector("a:has-text('로그아웃')", timeout=12000)
        return True
    except Exception:
        return False


async def _check_one(page, w: dict) -> dict | None:
    """Return {trains: [..], any_class_available: bool} when something is open."""
    await page.goto(S.SEARCH_URL, wait_until="domcontentloaded")
    await page.fill(S.DEPT_INPUT, w["origin"])
    await page.fill(S.ARRV_INPUT, w["destination"])
    await page.evaluate(
        "(args) => { const el = document.querySelector(args[1]); if(el){el.value=args[0];} }",
        [w["date"].replace("-", ""), S.DATE_INPUT],
    )
    hh = w["time"].split(":")[0].zfill(2)
    try:
        await page.select_option(S.TIME_SELECT, value=hh + "0000")
    except Exception:
        pass
    await page.click(S.SEARCH_BTN)
    try:
        await page.wait_for_selector(S.RESULT_ROWS, timeout=15000)
    except Exception:
        return None

    rows = await page.locator(S.RESULT_ROWS).all()
    found = []
    for r in rows:
        async def _txt(sel):
            try:
                return (await r.locator(sel).inner_text()).strip()
            except Exception:
                return ""
        dep = await _txt(S.ROW_DEPT_TIME)
        if not _within_window(dep, w["time"], int(w.get("window_minutes", 120))):
            continue
        try:
            first_html = await r.locator(S.ROW_FIRST_CLASS_BTN).inner_html()
        except Exception:
            first_html = ""
        std_cell = await _txt(S.ROW_SOLD_OUT_TXT)
        sold_first = not first_html or any(k in first_html for k in S.SOLD_OUT_KEYWORDS)
        sold_std = any(k in std_cell for k in S.SOLD_OUT_KEYWORDS) or "예매" not in std_cell
        if not sold_first or not sold_std:
            found.append(
                {
                    "train_no": await _txt(S.ROW_TRAIN_NO),
                    "depart": dep,
                    "arrive": await _txt(S.ROW_ARRV_TIME),
                    "first_class_available": not sold_first,
                    "standard_available": not sold_std,
                }
            )
    return {"trains": found} if found else None


def _load_alert_state() -> dict:
    if not ALERT_STATE_PATH.exists():
        return {}
    try:
        return json.loads(ALERT_STATE_PATH.read_text())
    except Exception:
        return {}


def _save_alert_state(s: dict) -> None:
    ALERT_STATE_PATH.write_text(json.dumps(s))


async def main() -> int:
    ko_id = os.environ.get("KORAIL_ID", "")
    ko_pw = os.environ.get("KORAIL_PW", "")
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not (ko_id and ko_pw and bot_token):
        print("[watcher] missing env (KORAIL_ID / KORAIL_PW / TELEGRAM_BOT_TOKEN). exit.")
        return 0

    if not WATCH_LIST_PATH.exists():
        print("[watcher] no watch_list.json — nothing to watch.")
        return 0
    watches = json.loads(WATCH_LIST_PATH.read_text())
    if not watches:
        return 0

    alert_state = _load_alert_state()
    now = int(datetime.utcnow().timestamp())

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--lang=ko-KR"])
        ctx = await browser.new_context(
            user_agent=UA,
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            viewport={"width": 1366, "height": 768},
        )
        page = await ctx.new_page()
        if not await _login(page, ko_id, ko_pw):
            await _send_telegram(
                bot_token,
                int(watches[0].get("owner_chat_id", 0) or 0),
                "🔐 [watcher] Korail 로그인 실패 또는 캡차. 다음 회차에 재시도합니다.",
            )
            await browser.close()
            return 0

        for w in watches:
            wid = w.get("id", "?")
            last = alert_state.get(wid, 0)
            if now - last < ALERT_COOLDOWN_SEC:
                print(f"[watcher] {wid} — cooldown, skip")
                continue
            try:
                hit = await _check_one(page, w)
            except Exception as exc:
                print(f"[watcher] {wid} error: {exc}")
                continue
            if not hit:
                print(f"[watcher] {wid} — sold out")
                continue
            best = hit["trains"][0]
            classes = []
            if best["standard_available"]:
                classes.append("일반실")
            if best["first_class_available"]:
                classes.append("특실")
            msg = (
                f"🎯 [watcher] 좌석 감지됨!\n"
                f"  {w['origin']} → {w['destination']} {w['date']} (목표 {w['time']} ±{w.get('window_minutes',120)}m)\n"
                f"  최근접 열차: {best['train_no']} {best['depart']}→{best['arrive']}\n"
                f"  가능 좌석: {', '.join(classes)}\n"
                f"PC 켜고 봇이 자동으로 잡도록 두거나, /resume 으로 즉시 진행하세요.\n"
                f"감시 ID: {wid}"
            )
            await _send_telegram(bot_token, int(w["owner_chat_id"]), msg)
            alert_state[wid] = now

        await browser.close()

    _save_alert_state(alert_state)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
