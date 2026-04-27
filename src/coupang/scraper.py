"""
Coupang scraping via the stealth browser.

Coupang aggressively blocks plain-HTTP scrapers. We instead reuse our long-lived
Chromium context (the same browser that holds our human-looking session), and
throttle every navigation to human pace.
"""

from __future__ import annotations

import asyncio
import random
import re
import urllib.parse
from datetime import datetime, timedelta
from typing import Any

from selectolax.parser import HTMLParser

from ..browser.humanize import human_pause, human_scroll
from ..browser.stealth import POOL
from ..config import CFG
from ..utils.log import get_logger

log = get_logger(__name__)


SEARCH_URL_TMPL = "https://www.coupang.com/np/search?q={q}&channel=user&listSize=36"


async def _polite_delay() -> None:
    s = CFG.coupang.scraping
    await asyncio.sleep(s.request_delay_sec + random.uniform(0, s.request_jitter_sec))


# ---------------------------------------------------------------------------
# Search results
# ---------------------------------------------------------------------------

async def search(query: str) -> list[dict[str, Any]]:
    page = await POOL.new_page()
    try:
        url = SEARCH_URL_TMPL.format(q=urllib.parse.quote(query))
        await page.goto(url, wait_until="domcontentloaded")
        await human_pause()
        await human_scroll(page, 1800, chunks=8)
        html = await page.content()
    finally:
        await page.close()

    tree = HTMLParser(html)
    items: list[dict[str, Any]] = []
    for li in tree.css("ul#productList li.search-product, li.baby-product"):
        a = li.css_first("a.search-product-link, a.baby-product-link")
        if not a:
            continue
        href = a.attributes.get("href", "")
        if not href.startswith("http"):
            href = "https://www.coupang.com" + href

        def _txt(sel: str) -> str:
            el = li.css_first(sel)
            return el.text(strip=True) if el else ""

        try:
            price_raw = _txt(".price-value, strong.price-value")
            price = int(re.sub(r"[^\d]", "", price_raw)) if price_raw else 0
        except ValueError:
            price = 0

        try:
            rating = float(_txt(".rating, em.rating") or 0.0)
        except ValueError:
            rating = 0.0

        review_raw = _txt(".rating-total-count, span.rating-total-count")
        try:
            review_count = int(re.sub(r"[^\d]", "", review_raw)) if review_raw else 0
        except ValueError:
            review_count = 0

        items.append(
            {
                "title": _txt(".name, .baby-product-name"),
                "url": href,
                "price_krw": price,
                "rating": rating,
                "review_count": review_count,
            }
        )
        if len(items) >= CFG.coupang.scraping.max_products_per_query:
            break
    log.info("coupang.search.done", q=query, count=len(items))
    return items


# ---------------------------------------------------------------------------
# Product reviews
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"(\d{4})\.(\d{1,2})\.(\d{1,2})")


def _parse_date(s: str) -> datetime | None:
    m = _DATE_RE.search(s)
    if not m:
        return None
    y, mo, d = (int(x) for x in m.groups())
    return datetime(y, mo, d)


async def fetch_reviews(
    product_url: str,
    *,
    max_reviews: int | None = None,
    cutoff_date: datetime | None = None,
) -> list[dict[str, Any]]:
    """
    Fetch reviews sorted by newest first.

    If `cutoff_date` is given, stop pagination as soon as we see a review older
    than the cutoff (since reviews are sorted newest-first, no point continuing).
    """
    max_reviews = max_reviews or CFG.coupang.scraping.max_reviews_per_product
    page = await POOL.new_page()
    reviews: list[dict[str, Any]] = []
    try:
        await page.goto(product_url, wait_until="domcontentloaded")
        await human_pause()
        # Scroll down so the review section is rendered.
        for _ in range(4):
            await human_scroll(page, 1200, chunks=6)
            await _polite_delay()

        # Switch sort to "최신순". Coupang exposes this as a select or a tab.
        try:
            await page.select_option("select.sdp-review__article-order", label="최신순")
            await _polite_delay()
        except Exception:
            try:
                tab = page.locator("button:has-text('최신순'), a:has-text('최신순')")
                if await tab.count():
                    await tab.first.click()
                    await _polite_delay()
            except Exception:
                pass  # may already be newest-first

        for _ in range(12):
            html = await page.content()
            tree = HTMLParser(html)
            page_had_old = False
            for art in tree.css("article.sdp-review__article-list, .sdp-review__article"):
                txt_el = art.css_first(".sdp-review__article-list__review__content, .review-content")
                star_el = art.css_first(".sdp-review__article-list__info__star-orange, .review-rating em")
                date_el = art.css_first(
                    ".sdp-review__article-list__info__product-info__reg-date, .review-date"
                )
                txt = txt_el.text(strip=True) if txt_el else ""
                if not txt:
                    continue
                star = 0
                if star_el:
                    try:
                        raw = star_el.attributes.get("data-rating") or star_el.text() or "0"
                        star = int(re.sub(r"[^\d]", "", raw))
                    except Exception:
                        star = 0
                date = _parse_date(date_el.text() if date_el else "")
                if cutoff_date and date and date < cutoff_date:
                    page_had_old = True
                    continue
                reviews.append({"text": txt, "rating": star, "date": date})
                if len(reviews) >= max_reviews:
                    return reviews

            if page_had_old and cutoff_date:
                # Sorted newest-first: once we see older-than-cutoff, we can stop.
                break

            nxt = page.locator(
                ".sdp-review__article-list-paging button.btn-next, button[aria-label='Next']"
            )
            if not await nxt.count():
                break
            try:
                await nxt.first.click()
                await _polite_delay()
            except Exception:
                break
    finally:
        await page.close()
    return reviews
