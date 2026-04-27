"""
Coupang product ranker.

Pipeline:
    search(query)
    → filter by price / must_include / must_exclude / min_review_count
    → for each survivor: fetch_reviews()
    → score(reviews) using:
        * recency weight (recent_review_window_days)
        * blacklist keyword filter (체험단, 무상으로 제공, ...)
        * Claude-based suitability score against `intent`
    → return Top 3
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from ..ai.claude import analyze_text
from ..config import CFG
from ..utils.log import get_logger
from . import scraper

log = get_logger(__name__)


# --- review filtering -------------------------------------------------------

def _is_suspicious(text: str) -> bool:
    return any(k in text for k in CFG.coupang.ranking.blacklist_keywords)


def _recency_weight(d: datetime | None, *, today: datetime) -> float:
    if d is None:
        return 0.3  # unknown date → small but non-zero
    age = (today - d).days
    win = CFG.coupang.ranking.recent_review_window_days
    if age <= win:
        return 1.0
    # decay: each window-length doubles age → halves weight
    return max(0.05, 0.5 ** (age / win))


# --- Claude-based suitability scoring --------------------------------------

SCORE_PROMPT = """\
당신은 쇼핑 어드바이저입니다. 아래 상품과 리뷰를 보고, 사용자의 의도/요구에 얼마나
적합한지 0~10점으로 평가합니다. 가짜 리뷰 의심 신호(과도한 칭찬, 광고성 문구,
체험단 흔적)도 함께 평가해 trust(0~1)로 별도 산출하세요.

반드시 JSON 한 줄만 출력:
{{"fit": <0..10>, "trust": <0..1>, "summary_ko": "<3문장 요약>"}}

[사용자 의도]
{intent}

[상품]
{title} — {price}원, 평균별점 {rating}, 리뷰 {review_count}건

[리뷰 샘플 — 최신순, 최대 25건]
{review_block}
"""


async def _llm_score(product: dict[str, Any], reviews: list[dict[str, Any]], intent: str) -> dict[str, Any]:
    today = datetime.utcnow()
    review_block = "\n".join(
        f"- ({r['date'].date() if r.get('date') else '날짜모름'}, {r['rating']}점) {r['text'][:300]}"
        for r in reviews[:25]
    ) or "(리뷰 없음)"

    prompt = SCORE_PROMPT.format(
        intent=intent or "(미지정)",
        title=product["title"],
        price=product["price_krw"],
        rating=product["rating"],
        review_count=product["review_count"],
        review_block=review_block,
    )
    try:
        raw = await analyze_text(prompt)
        # Be defensive: extract first JSON object
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("no JSON in LLM output")
        return json.loads(raw[start : end + 1])
    except Exception as exc:  # noqa: BLE001
        log.warning("llm_score.failed", err=str(exc))
        return {"fit": 5.0, "trust": 0.5, "summary_ko": "(분석 실패)"}


# --- Main ranker -----------------------------------------------------------

async def rank_products(
    query: str,
    *,
    max_price_krw: int = 0,
    must_include: list[str] | None = None,
    must_exclude: list[str] | None = None,
    intent: str = "",
) -> list[dict[str, Any]]:
    must_include = must_include or []
    must_exclude = must_exclude or []
    today = datetime.utcnow()

    items = await scraper.search(query)

    def _ok(it: dict[str, Any]) -> bool:
        if max_price_krw and it["price_krw"] > max_price_krw:
            return False
        if it["review_count"] < CFG.coupang.ranking.min_review_count:
            return False
        if it["rating"] and it["rating"] < CFG.coupang.ranking.min_avg_rating:
            return False
        title = it["title"]
        if any(w not in title for w in must_include):
            return False
        if any(w in title for w in must_exclude):
            return False
        return True

    survivors = [it for it in items if _ok(it)]
    log.info("coupang.filter", in_=len(items), out=len(survivors))
    survivors = survivors[:8]   # cap LLM calls

    scored: list[dict[str, Any]] = []
    for it in survivors:
        reviews = await scraper.fetch_reviews(it["url"])
        # Drop suspicious & require text reviews if configured
        clean = [
            r for r in reviews
            if (r.get("text") and not _is_suspicious(r["text"]))
        ]
        # Recency-weighted star score
        if clean:
            num = sum(r["rating"] * _recency_weight(r.get("date"), today=today) for r in clean)
            den = sum(_recency_weight(r.get("date"), today=today) for r in clean) or 1.0
            recency_score = num / den
        else:
            recency_score = 0.0

        llm = await _llm_score(it, clean, intent)
        # Final composite: recency★ * 1 + fit * 1 + trust * 5
        composite = recency_score + float(llm.get("fit", 5)) + 5 * float(llm.get("trust", 0.5))
        scored.append(
            {
                **it,
                "recency_score": round(recency_score, 2),
                "fit": llm.get("fit"),
                "trust": llm.get("trust"),
                "summary_ko": llm.get("summary_ko"),
                "composite": round(composite, 2),
                "review_sample_count": len(clean),
            }
        )

    scored.sort(key=lambda x: x["composite"], reverse=True)
    return scored[:3]


# --- Job entrypoint --------------------------------------------------------

async def run_coupang_job(job, notify: Callable[..., Awaitable[None]]) -> dict[str, Any]:
    p = job.params
    await notify(f"🛒 [{job.id}] 쿠팡 분석 시작 — '{p['query']}'")

    top = await rank_products(
        p["query"],
        max_price_krw=int(p.get("max_price_krw") or 0),
        must_include=p.get("must_include") or [],
        must_exclude=p.get("must_exclude") or [],
        intent=p.get("intent") or "",
    )

    if not top:
        await notify(f"😢 [{job.id}] 조건 만족 상품이 없습니다. 조건 완화 후 재요청해주세요.")
        return {"top": []}

    lines = [f"🏆 [{job.id}] 추천 Top {len(top)}"]
    for i, it in enumerate(top, 1):
        lines.append(
            f"\n{i}. {it['title']}\n"
            f"   💰 {it['price_krw']:,}원 / ⭐{it['rating']} ({it['review_count']:,}리뷰)\n"
            f"   적합도 {it['fit']}/10 · 신뢰도 {it['trust']} · 최신가중★ {it['recency_score']}\n"
            f"   📝 {it['summary_ko']}\n"
            f"   🔗 {it['url']}"
        )
    await notify("\n".join(lines))
    return {"top": top}
