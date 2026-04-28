"""
Human-like input primitives.

Bot detection on KR sites looks at:
  - perfect mouse straight-line moves,
  - simultaneous keystrokes (no inter-key delay),
  - 0ms scroll velocity,
  - missing focus/blur events.

These helpers emulate plausible human behaviour. Use them in place of
raw `page.click`, `page.fill`, `page.mouse.move`.
"""

from __future__ import annotations

import asyncio
import random
from typing import Tuple

from playwright.async_api import ElementHandle, Page

from ..config import CFG


def _rand_delay() -> float:
    return random.uniform(
        CFG.humanize.min_action_delay_ms / 1000,
        CFG.humanize.max_action_delay_ms / 1000,
    )


async def human_pause() -> None:
    await asyncio.sleep(_rand_delay())


async def human_arrival_pause() -> None:
    """페이지 이동 직후 사람이 화면 보고 적응하는 시간 — 2~5초.
    이걸 안 넣으면 봇 행동이 너무 즉각적이어서 탐지에 잘 걸린다."""
    await asyncio.sleep(random.uniform(1.8, 4.5))


async def human_idle_micro() -> None:
    """클릭/타이핑 직전의 짧은 망설임 — 100~600ms."""
    await asyncio.sleep(random.uniform(0.1, 0.6))


def _bezier(
    p0: Tuple[float, float],
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    p3: Tuple[float, float],
    steps: int,
):
    for i in range(steps + 1):
        t = i / steps
        x = (
            (1 - t) ** 3 * p0[0]
            + 3 * (1 - t) ** 2 * t * p1[0]
            + 3 * (1 - t) * t ** 2 * p2[0]
            + t ** 3 * p3[0]
        )
        y = (
            (1 - t) ** 3 * p0[1]
            + 3 * (1 - t) ** 2 * t * p1[1]
            + 3 * (1 - t) * t ** 2 * p2[1]
            + t ** 3 * p3[1]
        )
        yield x, y


async def human_mouse_to(page: Page, x: float, y: float) -> None:
    """Cubic-bezier mouse move with random control points and small jitter."""
    # Crude current pos (Playwright doesn't expose it; assume center as fallback).
    cur_x = getattr(page, "_last_mouse_x", 400.0)
    cur_y = getattr(page, "_last_mouse_y", 300.0)

    dx, dy = x - cur_x, y - cur_y
    cp1 = (cur_x + dx * 0.3 + random.uniform(-40, 40), cur_y + dy * 0.1 + random.uniform(-40, 40))
    cp2 = (cur_x + dx * 0.7 + random.uniform(-40, 40), cur_y + dy * 0.9 + random.uniform(-40, 40))
    steps = max(15, int(((dx ** 2 + dy ** 2) ** 0.5) / 12))

    for px, py in _bezier((cur_x, cur_y), cp1, cp2, (x, y), steps):
        await page.mouse.move(px + random.uniform(-0.7, 0.7), py + random.uniform(-0.7, 0.7))
        await asyncio.sleep(random.uniform(0.005, 0.018))

    page._last_mouse_x = x  # type: ignore[attr-defined]
    page._last_mouse_y = y  # type: ignore[attr-defined]


async def human_click(page: Page, selector: str, *, timeout: int = 15000) -> None:
    el = await page.wait_for_selector(selector, timeout=timeout, state="visible")
    if not el:
        raise RuntimeError(f"element not found: {selector}")
    box = await el.bounding_box()
    if not box:
        await el.click()
        return
    # Click on a random point inside the element (not the exact center).
    tx = box["x"] + box["width"] * random.uniform(0.25, 0.75)
    ty = box["y"] + box["height"] * random.uniform(0.30, 0.70)
    await human_mouse_to(page, tx, ty)
    # 클릭 직전 짧은 망설임 — 사람은 hover 후 잠깐 멈추고 누른다.
    await human_idle_micro()
    await page.mouse.down()
    await asyncio.sleep(random.uniform(0.04, 0.12))
    await page.mouse.up()
    await human_pause()


async def human_type(page: Page, selector: str, text: str) -> None:
    el = await page.wait_for_selector(selector, state="visible")
    if not el:
        raise RuntimeError(f"element not found: {selector}")
    await human_click(page, selector)
    # Clear field as a human would (select-all + delete) only if it has content.
    try:
        await el.evaluate("(e) => { e.value = ''; }")
    except Exception:
        pass
    lo, hi = CFG.humanize.type_delay_ms
    for ch in text:
        await page.keyboard.type(ch, delay=random.uniform(lo, hi))
        # Occasional thinking pause
        if random.random() < 0.04:
            await asyncio.sleep(random.uniform(0.18, 0.55))


async def human_scroll(page: Page, total_dy: int, *, chunks: int = 12) -> None:
    """Scroll in chunks with easing — mimics trackpad/wheel."""
    remaining = total_dy
    for i in range(chunks):
        # ease-in-out distribution
        frac = (i + 1) / chunks
        step = int(total_dy * (frac - i / chunks))
        step += random.randint(-12, 12)
        await page.mouse.wheel(0, step)
        remaining -= step
        await asyncio.sleep(random.uniform(0.08, 0.22))
    if remaining:
        await page.mouse.wheel(0, remaining)
