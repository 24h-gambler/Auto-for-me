"""
Stealth browser layer.

Goal: a long-lived Chromium instance that
  - persists cookies/localStorage (user_data_dir),
  - hides automation flags (playwright-stealth),
  - matches a believable Korean desktop fingerprint (UA / TZ / locale / viewport),
  - supports an outbound proxy if configured.

We expose a `BrowserPool` so multiple jobs share one browser and one context.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from playwright.async_api import (
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)
from playwright_stealth import Stealth

from ..config import ENV
from ..utils.log import get_logger

log = get_logger(__name__)


# A realistic, recent desktop Chrome on Windows 10 — common in KR.
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)


@dataclass
class BrowserPool:
    """Singleton-style holder for a persistent Chromium context."""

    pw: Optional[Playwright] = None
    ctx: Optional[BrowserContext] = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def start(self) -> None:
        if self.ctx:
            return
        async with self._lock:
            if self.ctx:
                return
            self.pw = await async_playwright().start()

            user_dir = Path(ENV.user_data_dir).resolve()
            user_dir.mkdir(parents=True, exist_ok=True)

            launch_kwargs = {
                "headless": ENV.headless,
                "user_data_dir": str(user_dir),
                "user_agent": DEFAULT_UA,
                "locale": "ko-KR",
                "timezone_id": "Asia/Seoul",
                "viewport": {"width": 1366, "height": 768},
                "device_scale_factor": 1,
                "color_scheme": "light",
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--lang=ko-KR",
                ],
            }
            if ENV.proxy_url:
                launch_kwargs["proxy"] = {"server": ENV.proxy_url}

            # `launch_persistent_context` returns a BrowserContext directly.
            self.ctx = await self.pw.chromium.launch_persistent_context(**launch_kwargs)

            # Apply stealth patches to every page in this context.
            await Stealth().apply_stealth_async(self.ctx)

            # Korean Accept-Language for every request.
            await self.ctx.set_extra_http_headers(
                {"Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"}
            )

            log.info("browser.started", headless=ENV.headless, profile=str(user_dir))

    async def new_page(self) -> Page:
        if not self.ctx:
            await self.start()
        assert self.ctx
        page = await self.ctx.new_page()
        # Block heavy resources we do not need (faster + less suspicious traffic).
        async def _route(route):
            t = route.request.resource_type
            if t in ("media", "font"):
                await route.abort()
            else:
                await route.continue_()
        await page.route("**/*", _route)
        return page

    async def stop(self) -> None:
        if self.ctx:
            await self.ctx.close()
            self.ctx = None
        if self.pw:
            await self.pw.stop()
            self.pw = None


POOL = BrowserPool()
