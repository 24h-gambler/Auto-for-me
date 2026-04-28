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
import random
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from playwright.async_api import Page

from ..browser.humanize import (
    human_arrival_pause,
    human_click,
    human_idle_micro,
    human_pause,
    human_type,
)
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


async def _dump_html(page: Page, label: str) -> Path:
    """디버깅용 — 페이지 전체 HTML 저장. 셀렉터 갱신할 때 본인이 이 파일 보내주면 됨."""
    out = Path("state/dumps")
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"{label}-{int(datetime.utcnow().timestamp())}.html"
    try:
        html = await page.content()
        p.write_text(html, encoding="utf-8")
    except Exception:
        pass
    return p


# ── 매크로 탐지 / 차단 페이지 회복 ─────────────────────────────────
# 새 코레일 사이트가 자동화를 잡으면 'CODE : -8003' / '매크로' 등의 메시지를
# 띄운다. 이걸 감지해 새로고침 + 긴 대기 + 알림 으로 회복한다.
BLOCK_MARKERS = (
    "CODE : -8003",
    "매크로",
    "미허가 도구",
    "비정상적인 접근",
    "이용이 제한",
)


async def detect_block(page: Page) -> Optional[str]:
    try:
        body = await page.locator("body").first.inner_text(timeout=2000)
    except Exception:
        return None
    for marker in BLOCK_MARKERS:
        if marker in body:
            return marker
    return None


async def recover_from_block(
    page: Page, *, marker: str, notify=None, job_id: str = ""
) -> None:
    """차단 화면 발견 → 스크린샷 + HTML 덤프 → 1~3분 대기 → 새로고침."""
    shot = await _capture_screenshot(page, f"block-{job_id or 'x'}")
    dump = await _dump_html(page, f"block-{job_id or 'x'}")
    log.warning("block.detected", marker=marker, shot=str(shot), dump=str(dump))
    if notify:
        await notify(
            f"🛑 차단 페이지 감지 ({marker}). 1~3분 쉬었다가 다시 시도합니다.\n"
            f"덤프: {dump.name}",
            photo_path=str(shot),
        )
    # 의도적으로 긴 임의 대기 — 봇 패턴 깨기.
    await asyncio.sleep(random.uniform(60, 180))
    try:
        await page.reload(wait_until="domcontentloaded")
    except Exception:
        try:
            await page.goto(S.HOME_URL, wait_until="domcontentloaded")
        except Exception:
            pass
    await human_arrival_pause()


# ---------------------------------------------------------------------------
# Popup dismissal — Korail shows event/notice popups that intercept clicks.
# ---------------------------------------------------------------------------

POPUP_CLOSE_SELECTORS = (
    "a:has-text('오늘 하루 보지 않기'), "
    "a:has-text('오늘 하루 안보기'), "
    "a:has-text('닫기'), button:has-text('닫기'), "
    ".popup_close, .btn_close, .layer_close, "
    "[aria-label='close'], [aria-label='닫기'], "
    "img[alt='닫기'], img[alt='close']"
)


async def dismiss_popups(page: Page) -> int:
    """Close any blocking popups/layers. Returns number closed. Best-effort."""
    closed = 0
    for _ in range(6):  # popups can be nested
        try:
            loc = page.locator(POPUP_CLOSE_SELECTORS).first
            if not await loc.count():
                break
            try:
                await loc.click(timeout=1500)
                closed += 1
                await asyncio.sleep(0.2)
            except Exception:
                break
        except Exception:
            break
    return closed


async def ensure_logged_in(page: Page, *, notify=None) -> None:
    await page.goto(S.HOME_URL, wait_until="domcontentloaded")
    await human_pause()
    await dismiss_popups(page)
    if await page.locator("a:has-text('로그아웃'), a[href*='logout']").count():
        return

    if not (ENV.korail_id and ENV.korail_pw):
        raise RuntimeError("KORAIL_ID / KORAIL_PW 가 .env 에 없습니다.")

    await page.goto(S.LOGIN_URL, wait_until="domcontentloaded")
    await dismiss_popups(page)
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
        await page.wait_for_selector(
            "a:has-text('로그아웃'), a[href*='logout']",
            timeout=15000,
        )
    except Exception as exc:
        raise CaptchaRequired("로그인 실패 (자격증명/캡차 오류 가능).") from exc
    await dismiss_popups(page)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

async def _select_station(page: Page, *, open_btn_sel: str, name: str) -> None:
    """역 팝업 열고 name 에 해당하는 역 선택 후 확인."""
    await page.click(open_btn_sel)
    await human_pause()
    # 팝업이 뜰 때까지 잠깐 대기
    try:
        await page.wait_for_selector(
            S.STATION_SEARCH_INPUT + ", " + S.STATION_LIST_ITEM_TPL.format(name=name),
            timeout=5000,
        )
    except Exception:
        pass
    # 1) 검색 input 이 있으면 역 이름 타이핑 — 후보 좁힘.
    try:
        si = page.locator(S.STATION_SEARCH_INPUT).first
        if await si.count():
            await si.fill("")
            await si.type(name, delay=80)
            await human_pause()
    except Exception:
        pass
    # 2) 역 이름 매칭하는 항목 클릭 — 가장 짧게 매칭되는 것을 우선.
    item_sel = S.STATION_LIST_ITEM_TPL.format(name=name)
    try:
        item = page.locator(item_sel).first
        await item.scroll_into_view_if_needed()
        await item.click(delay=80)
        await human_pause()
    except Exception as exc:  # noqa: BLE001
        log.warning("station.click_failed", name=name, err=str(exc))
        raise
    # 3) 확인 버튼 — 있으면 클릭 (어떤 팝업은 항목 클릭이 곧 확인).
    try:
        confirm = page.locator(S.STATION_CONFIRM_BTN).first
        if await confirm.count():
            await confirm.click(delay=80)
            await human_pause()
    except Exception:
        pass


async def _select_date(page: Page, date: str) -> None:
    """캘린더 팝업 열고 date(YYYY-MM-DD) 로 이동 후 일자 클릭."""
    await page.click(S.DATE_OPEN_BTN)
    await human_pause()
    try:
        await page.wait_for_selector(S.DATE_PICKER, timeout=5000)
    except Exception:
        pass
    yyyy, mm, dd = date.split("-")
    target_label = f"{yyyy}. {mm}."
    # 캘린더가 표시 중인 월이 target 보다 빠르면 next 클릭, 늦으면 prev.
    for _ in range(24):  # 최대 24회(=2년) 안전장치
        try:
            cur = (await page.locator(S.DATE_PICKER_MONTH_TXT).first.inner_text()).strip()
        except Exception:
            cur = ""
        if cur == target_label:
            break
        # 정렬 비교
        cur_norm = cur.replace(" ", "").rstrip(".")
        tgt_norm = target_label.replace(" ", "").rstrip(".")
        try:
            if cur_norm < tgt_norm:
                await page.click(S.DATE_PICKER_NEXT)
            else:
                await page.click(S.DATE_PICKER_PREV)
        except Exception:
            break
        await asyncio.sleep(0.3)

    day_sel = S.DATE_DAY_TPL.format(day=str(int(dd)))
    await page.click(day_sel, delay=80)
    await human_pause()


async def _select_hour(page: Page, time_str: str) -> None:
    """시간 picker 에서 HH:MM 의 HH 시 클릭. Slick 캐러셀에서 안 보이면 next 로 스크롤."""
    hour = str(int(time_str.split(":")[0]))
    sel = S.TIME_HOUR_TPL.format(hour=hour)
    for _ in range(8):
        try:
            loc = page.locator(sel).first
            if await loc.count():
                await loc.scroll_into_view_if_needed()
                await loc.click(delay=80)
                await human_pause()
                return
        except Exception:
            pass
        # 안 보이면 캐러셀 next
        try:
            nxt = page.locator(S.TIME_PICKER_NEXT).first
            if await nxt.count():
                await nxt.click(delay=80)
                await asyncio.sleep(0.2)
            else:
                break
        except Exception:
            break
    log.warning("time.hour_not_found", hour=hour)


async def search_trains(
    page: Page, *, origin: str, destination: str, date: str, time_str: str,
    notify=None, job_id: str = "",
) -> list[dict[str, Any]]:
    """새 korail.com 검색 흐름 — 출발역→도착역→날짜→시간→조회.
    각 단계마다 차단 페이지(-8003 등) 검사하고, 걸리면 회복 후 처음부터 재시도."""
    for attempt in range(3):
        await page.goto(S.SEARCH_URL, wait_until="domcontentloaded")
        await human_arrival_pause()
        await dismiss_popups(page)

        marker = await detect_block(page)
        if marker:
            await recover_from_block(page, marker=marker, notify=notify, job_id=job_id)
            continue

        try:
            await _select_station(page, open_btn_sel=S.DEPT_OPEN_BTN, name=origin)
            await _select_station(page, open_btn_sel=S.ARRV_OPEN_BTN, name=destination)
            await _select_date(page, date)
            await _select_hour(page, time_str)

            await human_idle_micro()
            await page.click(S.SEARCH_BTN)
            try:
                await page.wait_for_selector(S.RESULT_ROWS, timeout=20000)
            except Exception:
                log.warning("search.no_results_selector")

            marker = await detect_block(page)
            if marker:
                await recover_from_block(page, marker=marker, notify=notify, job_id=job_id)
                continue

            await dismiss_popups(page)
            break
        except Exception as exc:  # noqa: BLE001
            dump = await _dump_html(page, f"search-fail-{job_id or 'x'}")
            log.warning("search.failed", attempt=attempt, err=str(exc), dump=str(dump))
            if attempt == 2:
                if notify:
                    await notify(
                        f"⚠️ 검색 단계가 3회 실패. 페이지 HTML 덤프 저장됨:\n{dump}\n"
                        f"코드 갱신을 위해 이 파일 내용을 알려주세요."
                    )
                raise
            await asyncio.sleep(random.uniform(20, 60))

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
        std_has_avail = any(k in std_cell_html for k in S.AVAIL_KEYWORDS)
        sold_std = (not std_has_avail) or any(k in std_cell_html for k in S.SOLD_OUT_KEYWORDS)

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


async def _try_auto_assign(page: Page) -> bool:
    """좌석맵에 진입한 직후 '자동배정' 버튼이 보이면 그것을 클릭한다.
    자동배정은 코레일 기본 동작이고 좌석맵 DOM 변경에 영향 받지 않는 가장 안정적 경로."""
    try:
        loc = page.locator(S.AUTO_SEAT_BTN).first
        if await loc.count():
            await loc.scroll_into_view_if_needed()
            await loc.click(delay=80)
            await human_pause()
            return True
    except Exception:
        pass
    return False


async def _try_book_row(
    page: Page, row: dict[str, Any], *, seat_class: str, preferred_columns: list[str]
) -> Optional[dict[str, Any]]:
    """
    Click the appropriate '예매' button on the row, pick a seat (or 자동배정),
    and return booking info on success.
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

    await dismiss_popups(page)

    # 1) 우선 수동 좌석 picker 로 창가(A/D) 시도.
    seat = await _pick_seat(page, preferred_columns if seat_class == "standard" else ["A", "D"])
    # 2) 실패 시 '자동배정' 으로 fallback — 코레일이 좌석맵 대신 제공하는 안전 경로.
    if not seat:
        if await _try_auto_assign(page):
            seat = "자동배정"
        else:
            # back to results to try next row
            try:
                await page.go_back()
                await page.wait_for_selector(S.RESULT_ROWS, timeout=10000)
            except Exception:
                pass
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

async def _payment_visible(page: Page) -> bool:
    """결제 페이지 도달 여부 체크 — 메인 프레임 + 모든 iframe 검사 + 한국어 텍스트 검사."""
    # 1) CSS 마커 + 결제 버튼이 main frame / iframe 어디든 보이면 OK.
    css_locators = [
        page.locator(S.PAYMENT_PAGE_MARKER).first,
        page.locator(S.PAY_BTN).first,
    ]
    for fr in page.frames:
        if fr == page.main_frame:
            continue
        try:
            css_locators.append(fr.locator(S.PAYMENT_PAGE_MARKER).first)
            css_locators.append(fr.locator(S.PAY_BTN).first)
        except Exception:
            pass
    for loc in css_locators:
        try:
            if await loc.count():
                return True
        except Exception:
            continue

    # 2) 페이지에 결제 관련 한국어 텍스트가 보이는지 확인 (보조).
    for txt in S.PAYMENT_PAGE_TEXTS:
        try:
            if await page.get_by_text(txt, exact=False).first.count():
                return True
        except Exception:
            continue
    return False


async def navigate_to_payment(
    page: Page,
    *,
    notify=None,
    job_id: str = "",
    max_steps: int = 6,
) -> bool:
    """
    좌석 확보 후, '다음/예매하기/진행' 류 버튼을 차례로 눌러 결제 페이지에 도달합니다.
    결제 페이지에 도달하면 True, 아니면 False.

    `auto_pay=False` 여도 사용자가 결제 화면을 확실히 보고 카드선택만 하면 끝나도록
    결제 페이지까지 진입시킵니다. 실제 '결제하기' 최종 클릭은 이 함수가 하지 않습니다.
    각 단계마다 스크린샷을 남겨 어디서 막혔는지 사용자가 확인할 수 있게 합니다.
    """
    for step in range(max_steps):
        await dismiss_popups(page)

        if await _payment_visible(page):
            return True

        # 다음 단계 버튼 클릭. main frame + iframe 모두에서 탐색.
        clicked = False
        candidates = [page.locator(S.PROCEED_BTN).first]
        for fr in page.frames:
            if fr == page.main_frame:
                continue
            try:
                candidates.append(fr.locator(S.PROCEED_BTN).first)
            except Exception:
                pass

        for proceed in candidates:
            try:
                if not await proceed.count():
                    continue
                await proceed.scroll_into_view_if_needed()
                await proceed.click(delay=80)
                clicked = True
                await human_pause()
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:
                    pass
                break
            except Exception as exc:  # noqa: BLE001
                log.warning("payment.step_failed", step=step, err=str(exc))

        if notify and job_id:
            try:
                shot = await _capture_screenshot(page, f"step{step}-{job_id}")
                log.info("payment.step", step=step, shot=str(shot), clicked=clicked)
            except Exception:
                pass

        if not clicked:
            log.info("payment.no_proceed_btn", step=step)
            break

    return await _payment_visible(page)


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
# 사용자가 직접 검색까지 진행한 페이지를 받아 새로고침 + 클릭 반복하는 모드
# ---------------------------------------------------------------------------

async def _find_user_results_page() -> Optional[Page]:
    """결과 페이지 (또는 코레일 페이지) 찾기.
    CDP 모드면 사용자 진짜 Chrome 의 모든 컨텍스트 / 모든 탭을 검사."""
    pages: list[Page] = []
    # CDP 모드: 모든 컨텍스트의 모든 페이지 합치기
    if POOL.is_cdp and POOL.browser is not None:
        for ctx in POOL.browser.contexts:
            pages.extend(ctx.pages)
    elif POOL.ctx is not None:
        pages.extend(POOL.ctx.pages)
    if not pages:
        return None
    # 우선순위: 결과 페이지 URL 힌트 > 그 외 코레일 페이지 > 마지막
    for p in pages:
        try:
            if S.RESULT_PAGE_URL_HINT in p.url:
                return p
        except Exception:
            continue
    for p in pages:
        try:
            if "korail.com" in p.url:
                return p
        except Exception:
            continue
    return pages[-1]


async def _scan_and_reserve(
    page: Page, *, allow_standing: bool = True
) -> Optional[dict[str, Any]]:
    """결과 페이지의 보이는 행들에서 매진 아닌 셀 찾아 클릭 + 예매 버튼까지 누름.

    우선순위: 좌석(가격) > 입석+좌석.
    각 후보를 클릭한 뒤 .reservbtn 이 보이면 그것까지 클릭하고 종료.
    결제 페이지 진입은 호출측에서 navigate_to_payment 로 이어감.
    """
    candidates: list[tuple[str, Any]] = []

    # 1) 가격 표시된 좌석 링크 (일반실/특실)
    try:
        for el in await page.locator(S.SEAT_AVAIL_LINK).all():
            try:
                txt = (await el.inner_text()).strip()
            except Exception:
                continue
            if "매진" in txt and "임박" not in txt:
                continue
            if "원" not in txt:
                continue
            candidates.append(("seat", el))
    except Exception as exc:  # noqa: BLE001
        log.warning("scan.seat_failed", err=str(exc))

    # 2) 입석+좌석 (좌석이 다 매진일 때 fallback)
    if allow_standing:
        try:
            for el in await page.locator(S.STANDING_AVAIL_LINK).all():
                try:
                    txt = (await el.inner_text()).strip()
                except Exception:
                    continue
                if "입석" not in txt:
                    continue
                if "매진" in txt and "임박" not in txt:
                    continue
                candidates.append(("standing", el))
        except Exception as exc:  # noqa: BLE001
            log.warning("scan.standing_failed", err=str(exc))

    if not candidates:
        return None

    # 후보 클릭 → 예매 버튼까지 누름. 첫 성공 시 즉시 종료.
    for kind, el in candidates:
        try:
            txt = (await el.inner_text()).strip()
            await el.scroll_into_view_if_needed()
            await human_idle_micro()
            await el.click(delay=80)
            await human_pause()
        except Exception as exc:  # noqa: BLE001
            log.warning("scan.cell_click_failed", err=str(exc))
            continue

        # 셀 클릭 후 '예매' 버튼이 보일 때까지 잠시 대기 (UI 반응 시간).
        reserved = False
        for _ in range(6):
            try:
                btn = page.locator(S.RESERVE_BTN).first
                if await btn.count():
                    await btn.scroll_into_view_if_needed()
                    await human_idle_micro()
                    await btn.click(delay=80)
                    await human_pause()
                    reserved = True
                    break
            except Exception:
                pass
            await asyncio.sleep(0.3)

        return {"kind": kind, "cell_text": txt[:80], "reserved_btn_clicked": reserved}

    return None


async def _click_load_more(page: Page) -> bool:
    """'더보기' 클릭해서 다음 시간대 로드. 성공 시 True."""
    try:
        more = page.locator(S.LOAD_MORE_BTN).first
        if not await more.count():
            return False
        await more.scroll_into_view_if_needed()
        await human_idle_micro()
        await more.click(delay=80)
        await human_pause()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("scan.load_more_failed", err=str(exc))
        return False


# ── HYPER 모드 — JS 단일 호출로 스캔 + 클릭 ─────────────────────────
# Python ↔ Playwright ↔ Browser 왕복을 최소화 — 매 회차 거의 1회.
# 사람이 F5 누르고 보이는 즉시 누르는 속도 (≈0.5초 클릭반응) 와 동등.
def _build_scan_click_js(allow_standing: bool) -> str:
    return f"""
    () => {{
      const allowStanding = {str(allow_standing).lower()};
      const links = Array.from(document.querySelectorAll('a'));

      // 1순위: 가격 표시된 좌석 링크 (일반실/특실)
      for (const a of links) {{
        const txt = (a.innerText || '').trim();
        if (!txt) continue;
        if (txt.includes('매진') && !txt.includes('임박')) continue;
        if (a.querySelector('p.txt_gr, p.txt_price')) {{
          a.click();
          return {{ kind: 'seat', text: txt.slice(0, 80) }};
        }}
      }}

      // 2순위: 입석+좌석 (옵션)
      if (allowStanding) {{
        for (const a of links) {{
          const txt = (a.innerText || '').trim();
          if (!txt) continue;
          if (txt.includes('매진') && !txt.includes('임박')) continue;
          if (a.querySelector('.tck_etc_use') && txt.includes('입석')) {{
            a.click();
            return {{ kind: 'standing', text: txt.slice(0, 80) }};
          }}
        }}
      }}
      return null;
    }}
    """


_RESERVE_BTN_JS = """
() => {
  const direct = document.querySelector('button.reservbtn, button.btn_bn-blue02');
  if (direct && (direct.innerText || '').includes('예매')) { direct.click(); return true; }
  if (direct && !direct.innerText) { direct.click(); return true; }
  const fallback = Array.from(document.querySelectorAll('button')).find(b =>
    ((b.innerText || '').trim() === '예매')
  );
  if (fallback) { fallback.click(); return true; }
  return false;
}
"""


_BLOCK_CHECK_JS = """
() => {
  const t = (document.body && document.body.innerText) || '';
  return (
    t.includes('-8003') ||
    t.includes('매크로') ||
    t.includes('미허가 도구') ||
    t.includes('이용이 제한') ||
    t.includes('비정상적인 접근')
  );
}
"""


async def refresh_and_click_loop(
    job, notify: Callable[..., Awaitable[None]],
) -> dict[str, Any]:
    """사용자가 직접 검색까지 한 페이지에서 F5 따닥 + 셀 따닥 + 예매 따닥.

    설계 원칙:
      - Python ↔ 브라우저 왕복 최소화: scan + click 을 단일 page.evaluate 로.
      - human pause 없음 (속도가 우선).
      - 기본 0.8~2.0초 간격, aggressive 0.3~0.8초.
      - 매진만 있으면 그냥 reload 반복 — '더보기' 안 누름 (사용자가 미리
        필요한 시간대 펼쳐둔다는 가정).
      - 좌석 잡으면 즉시 button.reservbtn (예매) JS 클릭 → navigate_to_payment.
    """
    page = await _find_user_results_page()
    if page is None:
        await notify(
            f"❌ [{job.id}] 봇 브라우저에 코레일 페이지가 없습니다.\n"
            f"봇 chromium 창에서 직접 로그인 → 검색 결과 페이지까지 이동한 뒤 /refresh."
        )
        raise RuntimeError("no korail page in browser context")

    aggressive = bool(job.params.get("aggressive", False))
    allow_standing = bool(job.params.get("allow_standing", True))
    base_lo, base_hi = (0.3, 0.8) if aggressive else (0.8, 2.0)
    scan_click_js = _build_scan_click_js(allow_standing)

    await notify(
        f"⚡ [{job.id}] HYPER 새로고침 모드 시작\n"
        f"   페이지: {page.url}\n"
        f"   주기: {base_lo}~{base_hi}초\n"
        f"   잡힐 좌석: {'좌석+입석' if allow_standing else '좌석만'}\n"
        f"   F5 → 매진 아닌 셀 즉시 클릭 → 예매 버튼 즉시 클릭 → 결제 페이지."
    )

    deadline = CFG.ktx.poll.max_total_hours * 3600
    started = asyncio.get_event_loop().time()
    attempt = 0
    last_ping = started
    consecutive_errors = 0

    async def _try_finalize(result: dict[str, Any], reserve_clicked: bool) -> dict[str, Any]:
        """좌석 잡혔다 — 알림 + 결제 페이지까지 진입."""
        shot = await _capture_screenshot(page, f"hit-{job.id}")
        kind_kr = "좌석" if result["kind"] == "seat" else "입석+좌석"
        await notify(
            f"🔥🔥 [{job.id}] 잡았습니다! ({kind_kr})\n"
            f"   {result['text']}\n"
            f"   예매 버튼 {'✅ 클릭됨' if reserve_clicked else '⚠️ 못 찾음'}\n"
            f"   결제 페이지 진입 시도 중...",
            photo_path=str(shot),
        )
        # navigate_to_payment 가 다음 단계 (좌석/승객/결제) 진행
        reached = await navigate_to_payment(page, notify=notify, job_id=job.id)
        if reached:
            shot2 = await _capture_screenshot(page, f"payment-{job.id}")
            await notify(
                f"✅ [{job.id}] 결제 페이지 도달!\n"
                f"PC chromium 화면에서 결제수단 선택 → '결제하기'. (좌석 ≈10분 유지)",
                photo_path=str(shot2),
            )
            return {"reached_payment": True, "attempts": attempt, "kind": result["kind"]}
        shot3 = await _capture_screenshot(page, f"stuck-{job.id}")
        dump = await _dump_html(page, f"stuck-{job.id}")
        await notify(
            f"⚠️ [{job.id}] 좌석 잡았는데 결제 페이지 자동 진입 실패.\n"
            f"PC 화면에서 직접 다음 단계 진행. 좌석 ≈10분 유지.\n"
            f"덤프: {dump.name}",
            photo_path=str(shot3),
        )
        return {"reached_payment": False, "attempts": attempt, "kind": result["kind"]}

    while not job.cancel_event.is_set():
        attempt += 1
        now = asyncio.get_event_loop().time()
        if now - started > deadline:
            raise TimeoutError("HYPER 모드가 max_total_hours 를 초과했습니다.")

        # 1) 새로고침 (F5)
        try:
            await page.reload(wait_until="domcontentloaded", timeout=15000)
            consecutive_errors = 0
        except Exception as exc:  # noqa: BLE001
            consecutive_errors += 1
            log.warning("hyper.reload_failed", err=str(exc), n=consecutive_errors)
            if consecutive_errors > 5:
                await notify(f"⚠️ [{job.id}] 새로고침 5회 연속 실패. 30초 쉽니다.")
                await asyncio.sleep(30)
                consecutive_errors = 0
            else:
                await asyncio.sleep(0.5)
            continue

        # 2) 차단 페이지 검사 (JS 한 줄, 거의 즉시)
        try:
            blocked = await page.evaluate(_BLOCK_CHECK_JS)
        except Exception:
            blocked = False
        if blocked:
            await recover_from_block(page, marker="-8003/매크로", notify=notify, job_id=job.id)
            continue

        # 3) 스캔 + 클릭 (단일 JS 호출 — 가장 빠름)
        try:
            result = await page.evaluate(scan_click_js)
        except Exception as exc:  # noqa: BLE001
            log.warning("hyper.scan_failed", err=str(exc))
            result = None

        if result:
            # 4) 예매 버튼 즉시 클릭 (JS, 등장할 때까지 짧은 폴링)
            reserve_clicked = False
            for _ in range(20):  # 최대 ~2초
                try:
                    if await page.evaluate(_RESERVE_BTN_JS):
                        reserve_clicked = True
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.1)

            return await _try_finalize(result, reserve_clicked)

        # 5) 진행 알림 (50회마다 또는 30초마다)
        if attempt % 50 == 0 or now - last_ping > 30:
            await notify(f"⚡ [{job.id}] {attempt}회차 — 아직 매진. 계속 F5 중.")
            last_ping = now

        # 6) 짧은 랜덤 대기 후 다시 F5
        delay = random.uniform(base_lo, base_hi)
        try:
            await asyncio.wait_for(job.cancel_event.wait(), timeout=delay)
            raise asyncio.CancelledError()
        except asyncio.TimeoutError:
            pass

    raise asyncio.CancelledError("user cancelled hyper refresh loop")


# ---------------------------------------------------------------------------
# Job entrypoint
# ---------------------------------------------------------------------------

async def run_ktx_job(job, notify: Callable[..., Awaitable[None]]) -> dict[str, Any]:
    p = job.params
    strategy: str = p.get("seat_class_strategy") or "any"
    # refresh 전략은 사용자가 직접 검색해뒀으므로 출발/도착/일시 없어도 OK.
    origin: str = p.get("origin") or ""
    destination: str = p.get("destination") or ""
    date: str = p.get("date") or ""
    time_str: str = p.get("time") or ""
    window: int = int(p.get("window_minutes") or 120)
    preferred_cols: list[str] = p.get("preferred_columns") or ["A", "D"]
    hold_minutes: int = int(p.get("first_class_hold_minutes") or 8)
    # 빠른 폴링 모드 — 작업 단위로 toggle 가능. 미지정 시 config 따름.
    aggressive: bool = bool(p.get("aggressive", CFG.ktx.poll.aggressive))

    await POOL.start()

    # refresh 전략은 사용자가 직접 검색까지 한 페이지를 사용 — login/page 새로 안 만든다.
    if strategy == "refresh":
        return await refresh_and_click_loop(job, notify)

    page = await POOL.new_page()
    cadence = "🔥 빠른 폴링 (≈4초 간격)" if aggressive else "🐢 일반 폴링 (≈8~90초 백오프)"
    await notify(
        f"🚄 [{job.id}] {origin}→{destination} {date} {time_str} ±{window}분 시작\n"
        f"좌석 전략: {strategy}\n"
        f"창가 우선: {','.join(preferred_cols)}\n"
        f"폴링: {cadence}\n"
        f"매진 시 최대 {CFG.ktx.poll.max_total_hours}시간 동안 무한 폴링.\n"
        f"잡히면 결제 페이지까지 자동 진입 후 텔레그램 알림."
    )
    try:
        await ensure_logged_in(page, notify=notify)
    except CaptchaRequired as exc:
        await notify(f"🔐 [{job.id}] 인증 실패: {exc}")
        raise

    if aggressive:
        policy = BackoffPolicy(
            base=CFG.ktx.poll.aggressive_base_sec,
            cap=CFG.ktx.poll.aggressive_cap_sec,
            jitter=CFG.ktx.poll.aggressive_jitter_sec,
            factor=1.05,    # 거의 평탄
        )
    else:
        policy = BackoffPolicy(
            base=CFG.ktx.poll.base_interval_sec,
            cap=CFG.ktx.poll.max_interval_sec,
            jitter=CFG.ktx.poll.jitter_sec,
        )

    # ---- one search-and-attempt round, returns booking or None ----------
    async def attempt_standard() -> Optional[dict[str, Any]]:
        rows = await search_trains(
            page, origin=origin, destination=destination, date=date, time_str=time_str,
            notify=notify, job_id=job.id,
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

    async def attempt_any() -> Optional[dict[str, Any]]:
        """가장 빠른 좌석 1개 — 일반실 우선, 안 되면 같은 열차의 특실 즉시 시도.
        대기 / hold 로직 없이 잡히는 즉시 결제 페이지로 진입한다."""
        rows = await search_trains(
            page, origin=origin, destination=destination, date=date, time_str=time_str,
            notify=notify, job_id=job.id,
        )
        cands = [r for r in rows if _within_window(r["depart"], time_str, window)]
        cands.sort(key=lambda r: _time_distance(r["depart"], time_str))
        for row in cands:
            for cls, key in (("standard", "standard_available"), ("first", "first_class_available")):
                if not row[key]:
                    continue
                booked = await _try_book_row(
                    page, row, seat_class=cls,
                    preferred_columns=preferred_cols if cls == "standard" else ["A", "D"],
                )
                if booked:
                    return booked
        return None

    async def attempt_first_class() -> Optional[dict[str, Any]]:
        rows = await search_trains(
            page, origin=origin, destination=destination, date=date, time_str=time_str,
            notify=notify, job_id=job.id,
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
                        notify=notify, job_id=job.id,
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

    if strategy in ("any", "fast"):
        # 가장 빠른 좌석 1개 — 사용자 요구의 기본 모드.
        booked = await infinite_poll(
            target=attempt_any,
            policy=policy,
            deadline_sec=deadline,
            on_attempt=progress,
            cancel_event=job.cancel_event,
        )
    elif strategy == "first_class_only":
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
    reached = await navigate_to_payment(booking_page, notify=notify, job_id=job.id)
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
            f"🎯 [{job.id}] 좌석 잡았습니다 — 결제 페이지 진입 완료!\n"
            f"   🚄 {booked['train_no']}  {booked['depart']}→{booked['arrive']}\n"
            f"   💺 {booked['class']} {booked['seat']}\n"
            f"⏱️ 좌석 약 10분간 유지됩니다. PC 화면에서 결제수단 선택 → '결제하기' 클릭.\n"
            f"🔗 https://www.letskorail.com/  (마이페이지 → 결제대기 승차권에서도 결제 가능)",
            photo_path=str(shot),
        )

    return {"booked": booked, "attempts": attempts["n"], "reached_payment": True}
