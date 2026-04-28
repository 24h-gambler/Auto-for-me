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
    Browser,
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
    """Singleton-style holder for a persistent Chromium context.

    두 가지 모드 지원:
    1. CDP 연결 모드 (.env 의 CHROME_CDP_URL 가 설정되면)
       — 사용자가 미리 띄워둔 진짜 Chrome 에 connect_over_cdp 로 붙는다.
       자동화 흔적이 거의 없어 강한 anti-bot (코레일 등) 회피에 최적.
    2. 자체 launch 모드 (기본)
       — Playwright 가 chromium 을 직접 띄움. stealth 패치 적용.
    """

    pw: Optional[Playwright] = None
    ctx: Optional[BrowserContext] = None
    browser: Optional[Browser] = None     # CDP 모드 전용
    is_cdp: bool = False
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def start(self) -> None:
        if self.ctx:
            return
        async with self._lock:
            if self.ctx:
                return
            self.pw = await async_playwright().start()

            cdp_url = (getattr(ENV, "chrome_cdp_url", "") or "").strip()
            if cdp_url:
                await self._start_cdp(cdp_url)
            else:
                await self._start_launch()

    async def _start_cdp(self, cdp_url: str) -> None:
        """사용자가 띄운 진짜 Chrome 에 붙는다."""
        assert self.pw
        log.info("browser.cdp_connecting", url=cdp_url)
        self.browser = await self.pw.chromium.connect_over_cdp(cdp_url)
        # 보통 첫 context 가 사용자가 보고 있는 창. 없으면 새로 만들기.
        contexts = self.browser.contexts
        self.ctx = contexts[0] if contexts else await self.browser.new_context()
        self.is_cdp = True
        try:
            await self.ctx.set_extra_http_headers(
                {"Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"}
            )
        except Exception:
            pass
        log.info(
            "browser.cdp_connected",
            url=cdp_url,
            pages=len(self.ctx.pages),
            contexts=len(contexts),
        )

    async def _start_launch(self) -> None:
        """기본 — Playwright 가 chromium 을 직접 띄움."""
        assert self.pw
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
        proxy_url = (ENV.proxy_url or "").strip()
        if proxy_url:
            launch_kwargs["proxy"] = {"server": proxy_url}

        self.ctx = await self.pw.chromium.launch_persistent_context(**launch_kwargs)
        await Stealth().apply_stealth_async(self.ctx)
        await self.ctx.set_extra_http_headers(
            {"Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"}
        )
        log.info("browser.started", headless=ENV.headless, profile=str(user_dir))

    async def new_page(self) -> Page:
        if not self.ctx:
            await self.start()
        assert self.ctx
        page = await self.ctx.new_page()
        # CDP 모드(사용자 진짜 크롬)에선 라우트 가로채지 않음 — 자동화 흔적이 됨.
        if not self.is_cdp:
            async def _route(route):
                t = route.request.resource_type
                if t in ("media", "font"):
                    await route.abort()
                else:
                    await route.continue_()
            await page.route("**/*", _route)
        return page

    async def stop(self) -> None:
        if self.ctx and not self.is_cdp:
            try:
                await self.ctx.close()
            except Exception:
                pass
        self.ctx = None
        if self.browser and self.is_cdp:
            # CDP 모드에선 close() 하면 사용자 Chrome 까지 꺼지므로 disconnect 만.
            try:
                await self.browser.close()
            except Exception:
                pass
        self.browser = None
        if self.pw:
            try:
                await self.pw.stop()
            except Exception:
                pass
            self.pw = None
        self.is_cdp = False


POOL = BrowserPool()
