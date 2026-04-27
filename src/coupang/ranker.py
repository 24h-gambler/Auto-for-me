"""
Coupang product ranker — recency-first.

Pipeline
--------
search(query)
  → filter (price / must_include / must_exclude / minimum reviews)
  → for every survivor:
       fetch_reviews(sorted newest-first, cutoff = today - review_window_days)
       compute fresh_signal_score (recent only) + suspicion filter
  → rank by composite (recency_star × LLM_fit × LLM_trust × fresh_signal)
  → return Top N

Food categories (수박/사시미/회/고기/과일/야채 등) automatically use a 14-day
window and a prompt that emphasises 신선도/맛/당도. Non-food uses 90 days and
emphasises 적합도/내구성.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from ..ai.claude import analyze_text
from ..config import CFG
from ..utils.log import get_logger
from . import scraper

log = get_logger(__name__)


# --- food detection --------------------------------------------------------

FOOD_FRESH_KEYWORDS = (
    "수박", "참외", "딸기", "복숭아", "포도", "샤인머스캣", "사과", "배",
    "감귤", "귤", "오렌지", "망고", "체리", "토마토", "방울토마토",
    "사시미", "회", "육사시미", "육회", "삼겹", "한우", "소고기", "돼지고기",
    "닭", "오리", "랍스터", "킹크랩", "전복", "굴", "새우", "장어", "광어",
    "연어", "참치", "회덮밥", "샐러드", "야채", "채소", "버섯", "브로콜리",
    "고당도", "프리미엄", "특등급", "산지직송",
)


def detect_category(query: str, hint: str = "") -> str:
    if hint == "food_fresh":
        return "food_fresh"
    if any(k in query for k in FOOD_FRESH_KEYWORDS):
        return "food_fresh"
    return hint or "general"


# --- review filtering -------------------------------------------------------

def _is_suspicious(text: str) -> bool:
    return any(k in text for k in CFG.coupang.ranking.blacklist_keywords)


def _recency_weight(d: datetime | None, *, today: datetime, window_days: int) -> float:
    if d is None:
        return 0.2
    age = (today - d).days
    if age <= window_days:
        return 1.0
    return max(0.05, 0.5 ** (age / max(1, window_days)))


# --- LLM scoring -----------------------------------------------------------

FOOD_PROMPT = """\
당신은 신선식품 구매 어드바이저입니다. 다음 상품과 **최신 리뷰만** 보고 평가하세요.
판단 기준 (중요도 순):
1) 최근 리뷰의 신선도/맛/품질 평가가 일관되게 좋은지
2) 최근에 품질 저하 / 무름 / 상함 / 배송 지연 등 부정 신호가 있는지
3) 가짜/체험단 의심 (과도한 칭찬, 광고성 문구, 동일 표현 반복)

사용자 의도: {intent}
상품: {title} | {price}원 | 평균 ⭐{rating} ({review_count}개)

[최근 {window}일 리뷰 — 최신순]
{review_block}

JSON 한 줄로만 답하세요:
{{"freshness": 0~10, "taste": 0~10, "trust": 0~1, "recent_complaint": "<있으면 한 문장, 없으면 빈 문자열>", "summary_ko": "<3문장 요약>"}}
"""

GENERAL_PROMPT = """\
당신은 쇼핑 어드바이저입니다. 상품과 **최신 리뷰**를 바탕으로 평가하세요.
판단 기준:
1) 사용자 의도와의 적합도
2) 최근 사용자들의 만족도 / 단점
3) 가짜 리뷰 의심 신호

사용자 의도: {intent}
상품: {title} | {price}원 | 평균 ⭐{rating} ({review_count}개)

[최근 {window}일 리뷰 — 최신순]
{review_block}

JSON 한 줄로만 답하세요:
{{"fit": 0~10, "trust": 0~1, "recent_issue": "<있으면 한 문장, 없으면 빈 문자열>", "summary_ko": "<3문장 요약>"}}
"""


def _format_reviews(reviews: list[dict[str, Any]], limit: int = 30) -> str:
    if not reviews:
        return "(최신 리뷰 없음)"
    out = []
    for r in reviews[:limit]:
        d = r["date"].date().isoformat() if r.get("date") else "날짜모름"
        out.append(f"- ({d}, {r['rating']}점) {r['text'][:280]}")
    return "\n".join(out)


async def _llm_score(
    product: dict[str, Any], reviews: list[dict[str, Any]], intent: str, *, category: str, window: int
) -> dict[str, Any]:
    tmpl = FOOD_PROMPT if category == "food_fresh" else GENERAL_PROMPT
    prompt = tmpl.format(
        intent=intent or "(미지정)",
        title=product["title"],
        price=product["price_krw"],
        rating=product["rating"],
        review_count=product["review_count"],
        window=window,
        review_block=_format_reviews(reviews),
    )
    try:
        raw = await analyze_text(prompt)
        s = raw.find("{"); e = raw.rfind("}")
        if s == -1 or e == -1:
            raise ValueError("no JSON")
        return json.loads(raw[s : e + 1])
    except Exception as exc:  # noqa: BLE001
        log.warning("llm_score.failed", err=str(exc))
        if category == "food_fresh":
            return {"freshness": 5, "taste": 5, "trust": 0.5, "recent_complaint": "", "summary_ko": "(분석 실패)"}
        return {"fit": 5, "trust": 0.5, "recent_issue": "", "summary_ko": "(분석 실패)"}


# --- ranker ----------------------------------------------------------------

async def rank_products(
    query: str,
    *,
    max_price_krw: int = 0,
    must_include: list[str] | None = None,
    must_exclude: list[str] | None = None,
    intent: str = "",
    category_hint: str = "",
    review_window_days: int | None = None,
    top_n: int = 3,
) -> list[dict[str, Any]]:
    must_include = must_include or []
    must_exclude = must_exclude or []
    today = datetime.utcnow()

    category = detect_category(query, category_hint)
    if review_window_days is None:
        review_window_days = 14 if category == "food_fresh" else CFG.coupang.ranking.recent_review_window_days

    cutoff = today - timedelta(days=review_window_days)

    items = await scraper.search(query)

    def _ok(it: dict[str, Any]) -> bool:
        if max_price_krw and it["price_krw"] > max_price_krw:
            return False
        # food_fresh: be lenient on review_count (small sellers OK if recent reviews are good)
        min_reviews = 10 if category == "food_fresh" else CFG.coupang.ranking.min_review_count
        if it["review_count"] < min_reviews:
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
    log.info("coupang.filter", category=category, in_=len(items), out=len(survivors), window=review_window_days)
    survivors = survivors[: CFG.coupang.scraping.max_products_per_query]

    scored: list[dict[str, Any]] = []
    for it in survivors:
        reviews = await scraper.fetch_reviews(it["url"], cutoff_date=cutoff)
        clean = [r for r in reviews if r.get("text") and not _is_suspicious(r["text"])]
        # If we got 0 fresh reviews, this product is *uninteresting* for our use case.
        if not clean:
            log.info("coupang.skip_no_recent", title=it["title"][:40])
            continue

        # Recency-weighted star: heavily weight reviews inside `window`.
        num = sum(r["rating"] * _recency_weight(r.get("date"), today=today, window_days=review_window_days) for r in clean)
        den = sum(_recency_weight(r.get("date"), today=today, window_days=review_window_days) for r in clean) or 1.0
        recency_score = num / den

        llm = await _llm_score(it, clean, intent, category=category, window=review_window_days)

        if category == "food_fresh":
            composite = (
                recency_score * 1.0
                + float(llm.get("freshness", 5)) * 1.5
                + float(llm.get("taste", 5)) * 1.0
                + 6 * float(llm.get("trust", 0.5))
            )
        else:
            composite = (
                recency_score * 1.0
                + float(llm.get("fit", 5)) * 1.0
                + 5 * float(llm.get("trust", 0.5))
            )

        scored.append(
            {
                **it,
                "category": category,
                "recency_score": round(recency_score, 2),
                "fresh_review_count": len(clean),
                "llm": llm,
                "composite": round(composite, 2),
            }
        )

    scored.sort(key=lambda x: x["composite"], reverse=True)
    return scored[:top_n]


# --- job entry -------------------------------------------------------------

def _format_top(items: list[dict[str, Any]], job_id: str) -> str:
    if not items:
        return f"😢 [{job_id}] 조건에 맞는 상품이 없습니다. 조건을 완화하거나 다른 키워드로 시도해 주세요."
    lines = [f"🏆 [{job_id}] 추천 Top {len(items)}"]
    for i, it in enumerate(items, 1):
        llm = it["llm"]
        if it["category"] == "food_fresh":
            metric = (
                f"신선도 {llm.get('freshness')}/10 · 맛 {llm.get('taste')}/10 · 신뢰 {llm.get('trust')}"
                + (f"\n   ⚠️ 최근 불만: {llm['recent_complaint']}" if llm.get("recent_complaint") else "")
            )
        else:
            metric = (
                f"적합 {llm.get('fit')}/10 · 신뢰 {llm.get('trust')}"
                + (f"\n   ⚠️ 최근 이슈: {llm['recent_issue']}" if llm.get("recent_issue") else "")
            )
        lines.append(
            f"\n{i}. {it['title']}\n"
            f"   💰 {it['price_krw']:,}원 · 평균★{it['rating']} ({it['review_count']:,})\n"
            f"   📅 최신리뷰 {it['fresh_review_count']}건 · 최신가중★ {it['recency_score']}\n"
            f"   {metric}\n"
            f"   📝 {llm.get('summary_ko')}\n"
            f"   🔗 {it['url']}"
        )
    return "\n".join(lines)


async def run_coupang_job(job, notify: Callable[..., Awaitable[None]]) -> dict[str, Any]:
    p = job.params
    query = p["query"]
    category = detect_category(query, p.get("category_hint", ""))
    window = int(p.get("review_window_days") or (14 if category == "food_fresh" else 90))
    await notify(
        f"🛒 [{job.id}] '{query}' 분석 시작\n"
        f"   카테고리: {category} · 최신 리뷰 {window}일 기준"
    )

    top = await rank_products(
        query,
        max_price_krw=int(p.get("max_price_krw") or 0),
        must_include=p.get("must_include") or [],
        must_exclude=p.get("must_exclude") or [],
        intent=p.get("intent") or "",
        category_hint=p.get("category_hint") or "",
        review_window_days=window,
        top_n=int(p.get("top_n") or 3),
    )
    await notify(_format_top(top, job.id))
    return {"top": top}
