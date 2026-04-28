"""
KTX booking automation.

Strategy (default):
    1. Search the user's window (±N분).
    2. For each candidate train (sorted by closest to target time):
         a) Try 일반실 — pick a seat in column A or D (창가).
         b) If 일반실 매진, defer.
    3. If every candidate has 일반실 매진 in this round, optionally
       grab one 특실 seat as a *provisional hold* (~8분), notify user
       with countdown, and KEEP polling 일반실 in the background.
       - If 일반실 opens up while we're holding 특실, prefer 일반실 swap.
       - If user says "확정" → release the polling, finalize 특실.
       - If user says "release" or hold expires → drop the 특실, resume polling.

All actions go through `humanize.*` to avoid trivial bot patterns.
Captcha is never auto-solved — relayed via Telegram.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
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

class CaptchaRequired(Exception):
    pass


async def _capture_screenshot(page: Page, label: str) -> Path:
    out = Path("state/screenshots")
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"{label}-{int(datetime.utcnow().timestamp())}.png"
    await page.screenshot(path=str(p), full_page=False)
    return p


async def ensure_logged_in(page: Page, *, notify=None) -> None:
    await page.goto(S.HOME_URL, wait_until="domcontentloaded")
    await human_pause()
    if await page.locator("a:has-text('로그아웃')").count():
        return

    if not (ENV.korail_id and ENV.korail_pw):
        raise RuntimeError("KORAIL_ID / KORAIL_PW 가 .env 에 없습니다.")

    await page.goto(S.LOGIN_URL, wait_until="domcontentloaded")
    await human_type(page, S.ID_INPUT, ENV.korail_id)
    await human_type(page, S.PW_INPUT, ENV.korail_pw)

    if await page.locator(S.CAPTCHA_IMG).count():
        if notify is None:
            raise CaptchaRequired("로그인 캡차 (relay 핸들러 없음)")
        shot = await _capture_screenshot(page, "login-captcha")
        await notify(
            "🔐 로그인 캡차 발생. /captcha <코드> 로 5분 안에 입력해 주세요.",
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

async def search_trains(
    page: Page, *, origin: str, destination: str, date: str, time_str: str
) -> list[dict[str, Any]]:
    await page.goto(S.SEARCH_URL, wait_until="domcontentloaded")
    await human_pause()
    await human_type(page, S.DEPT_INPUT, origin)
    await human_type(page, S.ARRV_INPUT, destination)
    # 날짜는 readonly/datepicker 인 경우가 많아 value 만 바꾸면 폼 검증이 옛 값을 사용합니다.
    # input/change 이벤트를 dispatch 해 폼이 새 값을 받도록 강제합니다.
    await page.evaluate(
        """
        (args) => {
          const [val, sel] = args;
          const el = document.querySelector(sel);
          if (!el) return;
          el.value = val;
          el.dispatchEvent(new Event('input',  { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
        }
        """,
        [date.replace("-", ""), S.DATE_INPUT],
    )
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

        std_cell_html = ""
        first_cell_html = ""
        try:
            first_cell_html = await r.locator(S.ROW_FIRST_CLASS_BTN).inner_html()
        except Exception:
            first_cell_html = ""
        std_cell_html = await _txt(S.ROW_SOLD_OUT_TXT)

        sold_first = not first_cell_html or any(k in first_cell_html for k in S.SOLD_OUT_KEYWORDS)
        sold_std = any(k in std_cell_html for k in S.SOLD_OUT_KEYWORDS) or "예매" not in std_cell_html

        out.append(
            {
                "row": r,
                "train_no": await _txt(S.ROW_TRAIN_NO),
                "depart": await _txt(S.ROW_DEPT_TIME),
                "arrive": await _txt(S.ROW_ARRV_TIME),
                "first_class_available": not sold_first,
                "standard_available": not sold_std,
            }
        )
    return out


def _within_window(dep: str, target: str, window_min: int) -> bool:
    try:
        d = datetime.strptime(dep, "%H:%M")
        t = datetime.strptime(target, "%H:%M")
    except ValueError:
        return False
    return abs((d - t).total_seconds()) <= window_min * 60


def _time_distance(dep: str, target: str) -> int:
    try:
        d = datetime.strptime(dep, "%H:%M")
        t = datetime.strptime(target, "%H:%M")
        return int(abs((d - t).total_seconds()))
    except ValueError:
        return 10**9


# ---------------------------------------------------------------------------
# Seat picker (창가 A/D 우선)
# ---------------------------------------------------------------------------

_SEAT_RE = re.compile(r"^(\d+)\s*([A-Z])$")


async def _pick_seat(page: Page, preferred_columns: list[str]) -> Optional[str]:
    """
    On the seat-map page, pick the first available seat whose column letter is
    in `preferred_columns`. If none match, fall back to any available seat.
    Returns the seat name (e.g. '7A') or None if no seats are bookable.
    """
    try:
        await page.wait_for_selector(S.SEAT_BUTTONS, timeout=10000)
    except Exception:
        return None

    seats = await page.locator(S.SEAT_BUTTONS).all()
    available: list[tuple[str, Any]] = []
    for s in seats:
        try:
            name = await s.get_attribute(S.SEAT_NAME_ATTR) or ""
            status = await s.get_attribute(S.SEAT_AVAILABLE_ATTR) or ""
        except Exception:
            continue
        if status and status.lower() not in ("available", "0", "y", "yes"):
            continue
        if not name:
            # try alt/title fallback
            try:
                name = (await s.get_attribute("title")) or (await s.get_attribute("alt")) or ""
            except Exception:
                name = ""
        if name:
            available.append((name.strip().upper(), s))

    if not available:
        return None

    def _col(name: str) -> str:
        m = _SEAT_RE.match(name)
        return m.group(2) if m else ""

    # Preferred columns first, in user's order
    pref_set_order = {c.upper(): i for i, c in enumerate(preferred_columns)}
    available.sort(
        key=lambda x: (pref_set_order.get(_col(x[0]), 99), x[0])
    )

    name, el = available[0]
    try:
        await el.scroll_into_view_if_needed()
        await el.click(delay=80)
        await human_pause()
        if await page.locator(S.SEAT_CONFIRM_BTN).count():
            await human_click(page, S.SEAT_CONFIRM_BTN)
        return name
    except Exception:
        return None


async def _try_book_row(
    page: Page, row: dict[str, Any], *, seat_class: str, preferred_columns: list[str]
) -> Optional[dict[str, Any]]:
    """
    Click the appropriate '예매' button on the row, then pick a seat.
    Returns booking info dict on success, else None.
    """
    target_sel = S.ROW_FIRST_CLASS_BTN if seat_class == "first" else S.ROW_STD_CLASS_BTN
    btn = row["row"].locator(target_sel)
    if not await btn.count():
        return None
    try:
        box = await btn.first.bounding_box()
        if not box:
            return None
        await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, delay=80)
        await human_pause()
    except Exception:
        return None

    seat = await _pick_seat(page, preferred_columns if seat_class == "standard" else ["A", "D"])
    if not seat:
        # back to results to try next row
        await page.go_back()
        await page.wait_for_selector(S.RESULT_ROWS, timeout=10000)
        return None

    # We're now on the hold/payment-wait page.
    return {
        "train_no": row["train_no"],
        "depart": row["depart"],
        "arrive": row["arrive"],
        "class": "특실" if seat_class == "first" else "일반실",
        "seat": seat,
        "page": page,   # the page that holds the seat — used for payment navigation
    }


# ---------------------------------------------------------------------------
# Navigate to the actual payment screen
# ---------------------------------------------------------------------------

async def navigate_to_payment(page: Page, *, max_steps: int = 5) -> bool:
    """
    좌석 확보 후, '다음/예매하기/진행' 류 버튼을 차례로 눌러 결제 페이지에 도달합니다.
    결제 페이지에 도달하면 True. 도달 실패 (또는 예상 외 화면) 면 False.

    `auto_pay=False` 여도 사용자가 결제 화면을 확실히 볼 수 있도록 결제 페이지까지는
    무조건 진입시키는 게 목적입니다. 실제 '결제하기'(돈을 빼는 최종 버튼) 클릭은
    이 함수가 하지 않습니다.
    """
    for step in range(max_steps):
        # 이미 결제 페이지에 도달했는지 체크.
        try:
            if await page.locator(S.PAYMENT_PAGE_MARKER).first.count():
                return True
        except Exception:
            pass
        # PAY_BTN(='결제하기') 가 보이면 결제 페이지에 도달한 것이므로 멈춤.
        try:
            if await page.locator(S.PAY_BTN).first.count():
                return True
        except Exception:
            pass

        # 다음 단계 버튼 클릭.
        try:
            proceed = page.locator(S.PROCEED_BTN).first
            if not await proceed.count():
                log.info("payment.no_proceed_btn", step=step)
                return False
            await proceed.scroll_into_view_if_needed()
            await proceed.click(delay=80)
            await human_pause()
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
        except Exception as exc:  # noqa: BLE001
            log.warning("payment.step_failed", step=step, err=str(exc))
            return False

    # 마지막 한번 더 확인.
    try:
        return bool(
            await page.locator(S.PAY_BTN).first.count()
            or await page.locator(S.PAYMENT_PAGE_MARKER).first.count()
        )
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 특실 hold flow
# ---------------------------------------------------------------------------

async def _read_hold_seconds(page: Page) -> Optional[int]:
    """Try to read remaining hold seconds from the timer element. Best-effort."""
    try:
        txt = await page.locator(S.HOLD_TIMER_TXT).first.inner_text()
    except Exception:
        return None
    m = re.search(r"(\d+)\s*[:분]\s*(\d+)", txt)
    if not m:
        m2 = re.search(r"(\d+)", txt)
        return int(m2.group(1)) if m2 else None
    return int(m.group(1)) * 60 + int(m.group(2))


async def _hold_first_class_loop(
    page: Page,
    job,
    notify: Callable[..., Awaitable[None]],
    booked: dict[str, Any],
    *,
    hold_minutes: int,
    keep_polling_standard: Callable[[], Awaitable[Optional[dict[str, Any]]]],
) -> dict[str, Any]:
    """
    We hold a 특실 seat. Concurrently:
      - countdown ticker → ping user every 2 minutes,
      - keep_polling_standard() → if it finds 일반실, ask user to swap,
      - listen for user_decision queue: 'confirm' → keep 특실, 'release' → drop.
    """
    deadline = asyncio.get_event_loop().time() + hold_minutes * 60

    await notify(
        f"🪑 [{job.id}] 특실 임시 확보!\n"
        f"   {booked['train_no']} {booked['depart']}→{booked['arrive']} {booked['seat']}\n"
        f"⏳ {hold_minutes}분 안에 결제하지 않으면 자동 해제됩니다.\n"
        f"옵션:\n"
        f"  • '확정' / 예  → 특실 그대로 결제 진행\n"
        f"  • '취소' / 아니오 → 특실 풀고 일반실 계속 폴링\n"
        f"그동안 백그라운드에서 일반실도 계속 시도합니다."
    )

    standard_task = asyncio.create_task(keep_polling_standard())

    last_ping = asyncio.get_event_loop().time()
    try:
        while True:
            now = asyncio.get_event_loop().time()
            remaining = int(deadline - now)
            if remaining <= 0:
                # hold expired
                if not standard_task.done():
                    standard_task.cancel()
                await notify(f"⌛ [{job.id}] 특실 임시확보 시간 만료. 일반실 폴링을 재개합니다.")
                return {"outcome": "hold_expired", "first_class": booked}

            # 1) user decision
            try:
                decision = await asyncio.wait_for(job.user_decision.get(), timeout=5)
                if decision == "confirm":
                    if not standard_task.done():
                        standard_task.cancel()
                    return {"outcome": "first_class_confirmed", "booked": booked}
                if decision == "release":
                    if not standard_task.done():
                        standard_task.cancel()
                    await notify(f"♻️ [{job.id}] 특실 해제. 일반실 폴링 재개.")
                    return {"outcome": "released_by_user", "first_class": booked}
            except asyncio.TimeoutError:
                pass

            # 2) standard found in background?
            if standard_task.done():
                try:
                    std_booked = standard_task.result()
                except Exception:
                    std_booked = None
                if std_booked:
                    await notify(
                        f"🎉 [{job.id}] 일반실 확보! {std_booked['seat']}.\n"
                        f"특실 자동 해제하고 일반실로 진행합니다."
                    )
                    return {"outcome": "swapped_to_standard", "booked": std_booked}
                # restart polling
                standard_task = asyncio.create_task(keep_polling_standard())

            # 3) periodic ping
            if now - last_ping > 120:
                await notify(f"⏱️ [{job.id}] 특실 임시확보 잔여 {remaining // 60}분 {remaining % 60}초")
                last_ping = now

            if job.cancel_event.is_set():
                if not standard_task.done():
                    standard_task.cancel()
                raise asyncio.CancelledError("user cancelled during hold")
    finally:
        if not standard_task.done():
            standard_task.cancel()


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
    strategy: str = p.get("seat_class_strategy") or "standard_first_then_first_class_hold"
    preferred_cols: list[str] = p.get("preferred_columns") or ["A", "D"]
    hold_minutes: int = int(p.get("first_class_hold_minutes") or 8)

    await POOL.start()
    page = await POOL.new_page()
    await notify(
        f"🚄 [{job.id}] {origin}→{destination} {date} {time_str} ±{window}분 시작\n"
        f"좌석 전략: {strategy}\n"
        f"창가 우선: {','.join(preferred_cols)}\n"
        f"매진 시 최대 {CFG.ktx.poll.max_total_hours}시간 동안 무한 폴링."
    )
    try:
        await ensure_logged_in(page, notify=notify)
    except CaptchaRequired as exc:
        await notify(f"🔐 [{job.id}] 인증 실패: {exc}")
        raise

    policy = BackoffPolicy(
        base=CFG.ktx.poll.base_interval_sec,
        cap=CFG.ktx.poll.max_interval_sec,
        jitter=CFG.ktx.poll.jitter_sec,
    )

    # ---- one search-and-attempt round, returns booking or None ----------
    async def attempt_standard() -> Optional[dict[str, Any]]:
        rows = await search_trains(
            page, origin=origin, destination=destination, date=date, time_str=time_str
        )
        cands = [r for r in rows if _within_window(r["depart"], time_str, window)]
        cands.sort(key=lambda r: _time_distance(r["depart"], time_str))
        for row in cands:
            if not row["standard_available"]:
                continue
            booked = await _try_book_row(
                page, row, seat_class="standard", preferred_columns=preferred_cols
            )
            if booked:
                return booked
        return None

    async def attempt_first_class() -> Optional[dict[str, Any]]:
        rows = await search_trains(
            page, origin=origin, destination=destination, date=date, time_str=time_str
        )
        cands = [r for r in rows if _within_window(r["depart"], time_str, window)]
        cands.sort(key=lambda r: _time_distance(r["depart"], time_str))
        for row in cands:
            if not row["first_class_available"]:
                continue
            booked = await _try_book_row(
                page, row, seat_class="first", preferred_columns=["A", "D"]
            )
            if booked:
                return booked
        return None

    # ---- background task for hold-then-keep-polling ---------------------
    # IMPORTANT: 특실 임시확보 페이지가 떠 있는 `page` 를 침범하면 hold 화면이 사라집니다.
    # 백그라운드 일반실 폴링은 별도 page 를 만들어 그 위에서만 동작합니다.
    async def keep_polling_standard() -> Optional[dict[str, Any]]:
        bg_page = await POOL.new_page()
        try:
            await ensure_logged_in(bg_page, notify=notify)
        except Exception as exc:  # noqa: BLE001
            log.warning("hold.bg_login_failed", err=str(exc))
            await bg_page.close()
            return None
        try:
            while not job.cancel_event.is_set():
                try:
                    rows = await search_trains(
                        bg_page, origin=origin, destination=destination,
                        date=date, time_str=time_str,
                    )
                    cands = [r for r in rows if _within_window(r["depart"], time_str, window)]
                    cands.sort(key=lambda r: _time_distance(r["depart"], time_str))
                    for row in cands:
                        if not row["standard_available"]:
                            continue
                        booked = await _try_book_row(
                            bg_page, row, seat_class="standard",
                            preferred_columns=preferred_cols,
                        )
                        if booked:
                            return booked
                except Exception as exc:  # noqa: BLE001
                    log.warning("hold.bg_poll_failed", err=str(exc))
                await asyncio.sleep(policy.delay(2))
            return None
        finally:
            try:
                await bg_page.close()
            except Exception:
                pass

    deadline = CFG.ktx.poll.max_total_hours * 3600

    # ---- main strategy --------------------------------------------------
    attempts = {"n": 0}

    async def progress(attempt_no: int, delay: float):
        attempts["n"] = attempt_no
        if attempt_no % 10 == 0:
            await notify(f"⏳ [{job.id}] {attempt_no}회차 폴링, 다음 {delay:.0f}s 후")

    if strategy == "first_class_only":
        booked = await infinite_poll(
            target=attempt_first_class,
            policy=policy,
            deadline_sec=deadline,
            on_attempt=progress,
            cancel_event=job.cancel_event,
        )
    elif strategy == "standard_only":
        booked = await infinite_poll(
            target=attempt_standard,
            policy=policy,
            deadline_sec=deadline,
            on_attempt=progress,
            cancel_event=job.cancel_event,
        )
    else:
        # standard_first_then_first_class_hold
        # Round-robin: try standard for a few rounds; if nothing, opportunistically
        # grab 특실 hold and ask user.
        booked = None
        rounds_without_std = 0
        started = asyncio.get_event_loop().time()
        while booked is None:
            if job.cancel_event.is_set():
                raise asyncio.CancelledError()
            if asyncio.get_event_loop().time() - started > deadline:
                raise TimeoutError(f"KTX 폴링이 {CFG.ktx.poll.max_total_hours}시간을 초과했습니다.")
            attempts["n"] += 1
            booked = await attempt_standard()
            if booked:
                booked["class"] = "일반실"
                break
            rounds_without_std += 1

            # After 3 rounds without 일반실, try to opportunistically hold 특실.
            if rounds_without_std >= 3:
                first = await attempt_first_class()
                if first:
                    outcome = await _hold_first_class_loop(
                        page,
                        job,
                        notify,
                        first,
                        hold_minutes=hold_minutes,
                        keep_polling_standard=keep_polling_standard,
                    )
                    o = outcome["outcome"]
                    if o == "first_class_confirmed":
                        booked = outcome["booked"]
                        break
                    if o == "swapped_to_standard":
                        booked = outcome["booked"]
                        break
                    # released or expired → reset and continue polling 일반실
                    rounds_without_std = 0
                    await notify(f"🔁 [{job.id}] 일반실 폴링 재개.")
                    continue

            delay = policy.delay(attempts["n"])
            await progress(attempts["n"], delay)
            await asyncio.sleep(delay)

    # We have a booking. The page that holds the seat may differ from `page`
    # (e.g. when the background polling task succeeded with `bg_page`).
    booking_page: Page = booked.pop("page", page)

    # 무조건 결제 페이지까지 진입시킨다 — 사용자 요구.
    reached = await navigate_to_payment(booking_page)
    if not reached:
        shot = await _capture_screenshot(booking_page, f"stuck-{job.id}")
        await notify(
            f"⚠️ [{job.id}] 좌석은 확보됐지만 결제 페이지까지 자동 진입하지 못했습니다.\n"
            f"   {booked['train_no']} {booked['depart']}→{booked['arrive']} "
            f"{booked['class']} {booked['seat']}\n"
            f"코레일 앱/웹에서 직접 결제 마무리해 주세요. 좌석은 약 10분간 유지됩니다.",
            photo_path=str(shot),
        )
        return {"booked": booked, "attempts": attempts["n"], "reached_payment": False}

    shot = await _capture_screenshot(booking_page, f"payment-{job.id}")
    if CFG.ktx.auto_pay:
        try:
            await human_click(booking_page, S.PAY_BTN)
            await notify(
                f"💳 [{job.id}] 결제 자동 진행 — {booked['train_no']} {booked['seat']} ({booked['class']})",
                photo_path=str(shot),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("auto_pay.failed", err=str(exc))
            await notify(
                f"⚠️ [{job.id}] 결제 페이지까지 도달했으나 자동 결제 클릭 실패: {exc}\n"
                f"화면에서 직접 '결제하기' 를 눌러주세요.",
                photo_path=str(shot),
            )
    else:
        await notify(
            f"🎯 [{job.id}] 결제 페이지까지 도달!\n"
            f"   {booked['train_no']} {booked['depart']}→{booked['arrive']}\n"
            f"   {booked['class']} {booked['seat']}\n"
            f"화면에서 결제 수단을 선택하고 '결제하기' 를 눌러주세요. (좌석 ≈10분 유지)",
            photo_path=str(shot),
        )

    return {"booked": booked, "attempts": attempts["n"], "reached_payment": True}
