"""
Anthropic SDK wrapper.

- Uses claude-opus-4-7 for the orchestrator (tool-using agent that decides
  whether the user wants a KTX booking, a Coupang recommendation, etc.).
- Uses claude-sonnet-4-6 for cheap, high-volume review trustworthiness scoring.
- Enables prompt caching on the system prompt so repeated tool-use turns are
  cheap.
"""

from __future__ import annotations

from typing import Any, Iterable

from anthropic import AsyncAnthropic

from ..config import ENV
from ..utils.log import get_logger

log = get_logger(__name__)

_client: AsyncAnthropic | None = None


def client() -> AsyncAnthropic:
    global _client
    if _client is None:
        if not ENV.anthropic_api_key:
            raise RuntimeError(
                "Anthropic API 키가 없습니다. 자유채팅(자연어 → 작업) 또는 쿠팡 분석을 "
                "쓰려면 console.anthropic.com 에서 키를 발급해 .env 의 "
                "ANTHROPIC_API_KEY 에 넣어주세요. "
                "슬래시 명령(/ktx, /coupang 직접 파라미터)만 쓰는 경우엔 필요 없습니다."
            )
        _client = AsyncAnthropic(api_key=ENV.anthropic_api_key)
    return _client


async def chat(
    *,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    model: str | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.2,
) -> Any:
    """Single-turn chat (caller drives the tool-use loop)."""
    return await client().messages.create(
        model=model or ENV.orchestrator_model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=[
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        tools=tools or [],
        messages=messages,
    )


async def analyze_text(prompt: str, *, model: str | None = None, max_tokens: int = 1024) -> str:
    """One-shot text analysis (for review scoring etc.). Returns a string."""
    resp = await client().messages.create(
        model=model or ENV.analyzer_model,
        max_tokens=max_tokens,
        temperature=0.0,
        messages=[{"role": "user", "content": prompt}],
    )
    parts: Iterable[Any] = resp.content
    return "".join(b.text for b in parts if getattr(b, "type", "") == "text")
