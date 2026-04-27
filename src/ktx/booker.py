"""
KTX booking automation with infinite sold-out polling.

Flow
----
1. ensure_logged_in()  → resume session if cookies still valid
2. search_trains()     → submit the search form, parse result rows
3. pick_candidate()    → first non-sold-out train within the user's window,
                         honoring class preference (특실 > 일반실 by config)
4. proceed_to_seat()   → click 예매 → seat picker → 결제대기
5. notify_user()       → if auto_pay=False, stop here and ask the user via Telegram

If every candidate is sold out → wait `BackoffPolicy.delay()` and re-search.
We never give up unless cancel_event is set or `max_total_hours` elapses.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from playwright.async_api import Page

from ..browser.humanize import human_click, human_pause, human_type
from ..browser.stealth import POOL
from ..config import CFG, ENV
from ..utils.log import get_logger
from ..utils.retry import BackoffPolicy, infinite_poll
from . import selectors as S

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Login / session
# ---------------------------------------------------------------------------

async def ensure_logged_in(page: Page, *, notify=None) -> None:
    await page.goto(S.HOME_URL, wait_until="domcontentloaded")
    await human_pause()
    # If 로그아웃 link is visible we already have a session.
    if await page.locator("a:has-text('로그아웃')").count():
        return

    if not (ENV.korail_id and ENV.korail_pw):
        raise RuntimeError(
            "코레일 자격증명이 .env 에 없습니다. KORAIL_ID / KORAIL_PW 를 설정하세요."
        )

    await page.goto(S.LOGIN_URL, wait_until="domcontentloaded")
    await human_type(page, S.ID_INPUT, ENV.korail_id)
    await human_type(page, S.PW_INPUT, ENV.korail_pw)

    # If a captcha image is present, relay via Telegram before submitting.
    if await page.locator(S.CAPTCHA_IMG).count():
        if notify is None:
            raise CaptchaRequired("로그인 캡차 발생 (relay 핸들러 없음).")
        shot = await _capture_screenshot(page, "login-captcha")
        await notify(
            "🔐 로그인 캡차 발생. /captcha <코드> 로 입력해 주세요 (5분 이내).",
            photo_path=str(shot),
        )
        from ..notify.telegram import CAPTCHA_QUEUE
        try:
            code = await asyncio.wait_for(CAPTCHA_QUEUE.get(), timeout=300)
        except asyncio.TimeoutError as exc:
            raise CaptchaRequired("캡차 입력 시간 초과") from exc
        await human_type(page, S.CAPTCHA_INPUT, code)

    await human_click(page, S.LOGIN_BTN)
    try:
        await page.wait_for_selector("a:has-text('로그아웃')", timeout=15000)
    except Exception as exc:
        raise CaptchaRequired("로그인 실패 (자격증명/캡차 오류 가능).") from exc


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

async def search_trains(page: Page, *, origin: str, destination: str, date: str, time_str: str) -> list[dict[str, Any]]:
    await page.goto(S.SEARCH_URL, wait_until="domcontentloaded")
    await human_pause()

    await human_type(page, S.DEPT_INPUT, origin)
    await human_type(page, S.ARRV_INPUT, destination)

    # Date input may be a masked field
    await page.evaluate(
        "(d) => { const el = document.querySelector(arguments[1]); if(el){el.value=d;} }",
        date.replace("-", ""),
        S.DATE_INPUT,
    )

    # Time dropdown — pick nearest hour <= requested hour (Korail rounds down).
    hh = time_str.split(":")[0].zfill(2)
    try:
        await page.select_option(S.TIME_SELECT, value=hh + "0000")
    except Exception:
        pass

    await human_click(page, S.SEARCH_BTN)
    await page.wait_for_selector(S.RESULT_ROWS, timeout=20000)

    rows = await page.locator(S.RESULT_ROWS).all()
    out: list[dict[str, Any]] = []
    for r in rows:
        async def _txt(sel: str) -> str:
            try:
                return (await r.locator(sel).inner_text()).strip()
            except Exception:
                return ""

        train_no = await _txt(S.ROW_TRAIN_NO)
        dep = await _txt(S.ROW_DEPT_TIME)
        arr = await _txt(S.ROW_ARRV_TIME)
        std_cell = await _txt(S.ROW_SOLD_OUT_TXT)
        first_cell_html = ""
        try:
            first_cell_html = await r.locator(S.ROW_FIRST_CLASS_BTN).inner_html()
        except Exception:
            first_cell_html = ""

        sold_first = not first_cell_html or any(k in first_cell_html for k in S.SOLD_OUT_KEYWORDS)
        sold_std = any(k in std_cell for k in S.SOLD_OUT_KEYWORDS)

        out.append(
            {
                "row": r,
                "train_no": train_no,
                "depart": dep,
                "arrive": arr,
                "first_class_available": not sold_first,
                "standard_available": not sold_std,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Candidate selection (window + class preference)
# ---------------------------------------------------------------------------

def _within_window(dep: str, target: str, window_min: int) -> bool:
    """dep / target are 'HH:MM' strings."""
    try:
        d = datetime.strptime(dep, "%H:%M")
        t = datetime.strptime(target, "%H:%M")
    except ValueError:
        return False
    return abs((d - t).total_seconds()) <= window_min * 60


async def _try_book_row(page: Page, row: dict[str, Any], *, prefer_first_class: bool) -> bool:
    target_sel = S.ROW_FIRST_CLASS_BTN if prefer_first_class else S.ROW_STD_CLASS_BTN
    btn = row["row"].locator(target_sel)
    if not await btn.count():
        return False
    box = await btn.first.bounding_box()
    if not box:
        return False
    await page.mouse.click(
        box["x"] + box["width"] / 2,
        box["y"] + box["height"] / 2,
        delay=80,
    )
    await human_pause()
    # If we landed on the seat-pick page, we're good.
    return await page.locator(S.PROCEED_BTN).count() > 0


# ---------------------------------------------------------------------------
# Captcha relay
# ---------------------------------------------------------------------------

class CaptchaRequired(Exception):
    pass


async def _capture_screenshot(page: Page, label: str) -> Path:
    out = Path("state/screenshots")
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"{label}-{int(datetime.utcnow().timestamp())}.png"
    await page.screenshot(path=str(p), full_page=False)
    return p


# ---------------------------------------------------------------------------
# Job entrypoint
# ---------------------------------------------------------------------------

async def run_ktx_job(job, notify: Callable[..., Awaitable[None]]) -> dict[str, Any]:
    p = job.params
    origin: str = p["origin"]
    destination: str = p["destination"]
    date: str = p["date"]
    time_str: str = p["time"]
    window: int = int(p.get("window_minutes") or 120)

    await POOL.start()
    page = await POOL.new_page()

    await notify(
        f"🚄 [{job.id}] {origin}→{destination} {date} {time_str} ±{window}분 폴링 시작\n"
        f"매진 풀릴 때까지 {CFG.ktx.poll.max_total_hours}시간 동안 자동 재시도합니다."
    )

    try:
        await ensure_logged_in(page, notify=notify)
    except CaptchaRequired as exc:
        await notify(f"🔐 [{job.id}] 로그인 인증 단계 실패: {exc}. 작업 종료.")
        raise

    policy = BackoffPolicy(
        base=CFG.ktx.poll.base_interval_sec,
        cap=CFG.ktx.poll.max_interval_sec,
        jitter=CFG.ktx.poll.jitter_sec,
    )

    async def attempt() -> Optional[dict[str, Any]]:
        rows = await search_trains(
            page,
            origin=origin,
            destination=destination,
            date=date,
            time_str=time_str,
        )
        candidates = [r for r in rows if _within_window(r["depart"], time_str, window)]
        if not candidates:
            log.info("ktx.no_candidates", searched=len(rows))
            return None

        # honor seat preference
        prefer_first = "특실" in CFG.ktx.seat_preference and CFG.ktx.seat_preference.index("특실") < CFG.ktx.seat_preference.index("일반실")

        for row in candidates:
            for prefer in (prefer_first, not prefer_first):
                key = "first_class_available" if prefer else "standard_available"
                if not row[key]:
                    continue
                ok = await _try_book_row(page, row, prefer_first_class=prefer)
                if ok:
                    return {
                        "train_no": row["train_no"],
                        "depart": row["depart"],
                        "arrive": row["arrive"],
                        "class": "특실" if prefer else "일반실",
                    }
        return None  # all sold out → keep polling

    deadline = CFG.ktx.poll.max_total_hours * 3600
    attempts = {"n": 0}

    async def progress(attempt_no: int, delay: float) -> None:
        attempts["n"] = attempt_no
        if attempt_no % 10 == 0:
            await notify(f"⏳ [{job.id}] {attempt_no}회차 — 다음 재시도 {delay:.0f}s 후")

    booked = await infinite_poll(
        target=attempt,
        policy=policy,
        deadline_sec=deadline,
        on_attempt=progress,
        cancel_event=job.cancel_event,
    )

    # We have a seat held. Either auto-pay or hand off to user.
    if CFG.ktx.auto_pay and await page.locator(S.PAY_BTN).count():
        await human_click(page, S.PAY_BTN)
        await notify(
            f"💳 [{job.id}] 결제 진행 — 좌석 {booked['train_no']} {booked['depart']}→{booked['arrive']} ({booked['class']})"
        )
    else:
        shot = await _capture_screenshot(page, f"hold-{job.id}")
        await notify(
            f"🪑 [{job.id}] 좌석 확보! {booked['train_no']} {booked['depart']}→{booked['arrive']} ({booked['class']})\n"
            f"코레일 앱/웹에서 10분 안에 결제 완료해 주세요.",
            photo_path=str(shot),
        )

    return {"booked": booked, "attempts": attempts["n"]}
