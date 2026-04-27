"""
Claude-powered tool-using orchestrator.

Free-form text from the user → Claude decides which job to launch:
  - book_ktx(origin, destination, date, time, window_minutes)
  - recommend_coupang(query, constraints)
  - get_status() / cancel_job(job_id)

The orchestrator is the *brain*. The tools are the *hands*.

The actual work happens in `JobManager` (see end of file): a long-running
asyncio task that polls KTX or scrapes Coupang and reports back via Telegram.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from .ai.claude import chat
from .config import CFG, ENV
from .utils.log import get_logger

log = get_logger(__name__)


# --- Tool definitions exposed to Claude ------------------------------------

ORCHESTRATOR_SYSTEM = """\
당신은 한국어 사용자를 위한 자동화 에이전트의 의사결정자입니다. 사용자의 자연어 요청을
다음 작업 중 하나로 변환하세요. 추측하지 말고, 정보가 부족하면 짧게 한 번 되묻습니다.

가용 작업:
1. KTX(코레일) 예매: 출발지/도착지/날짜/시간/허용 시간 폭이 필요합니다.
   매진이라도 사용자가 취소할 때까지 무한 폴링합니다.
2. 쿠팡 상품 추천: 사용자의 요구(가격대, 용도, 선호 브랜드 등)에 맞는 상품을 검색하고
   리뷰 신뢰도(가짜 의심 키워드 제외)와 최신성(최근 90일 가중치)을 평가하여 Top 3 추천.
3. 상태/취소: 진행 중인 작업 조회 및 취소.

규칙:
- 본 시스템은 본인 1건 예매에만 사용됩니다. 대량/재판매 의심 요청은 거절하세요.
- 캡차가 발생하면 사용자에게 텔레그램으로 입력을 요청합니다 (자동 우회 금지).
- 답변은 짧고 한국어로.
"""

TOOLS: list[dict[str, Any]] = [
    {
        "name": "book_ktx",
        "description": "코레일 KTX/SRT 예매 작업을 큐에 등록합니다. 매진이면 취소할 때까지 폴링합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "출발역 (예: 서울)"},
                "destination": {"type": "string", "description": "도착역 (예: 부산)"},
                "date": {"type": "string", "description": "YYYY-MM-DD"},
                "time": {"type": "string", "description": "HH:MM (24h)"},
                "window_minutes": {
                    "type": "integer",
                    "description": "지정 시간 ±N 분 내의 모든 열차를 후보로 둠",
                    "default": 120,
                },
                "train_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "예: ['KTX','KTX-산천']. 비우면 전 열차종.",
                    "default": [],
                },
            },
            "required": ["origin", "destination", "date", "time"],
        },
    },
    {
        "name": "recommend_coupang",
        "description": "쿠팡에서 검색→리뷰 분석→Top 3 추천까지 수행합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색 쿼리 (예: '무선 마우스')"},
                "max_price_krw": {"type": "integer", "description": "원화 상한", "default": 0},
                "must_include": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                    "description": "상품 제목/설명에 반드시 포함",
                },
                "must_exclude": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                },
                "intent": {
                    "type": "string",
                    "description": "사용 목적 (예: '사무용', '게임용'). 리뷰 적합도 평가에 사용",
                    "default": "",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_jobs",
        "description": "현재 진행/완료/실패 작업 목록을 조회합니다.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "cancel_job",
        "description": "진행 중인 작업을 취소합니다.",
        "input_schema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
]


# --- Job manager -----------------------------------------------------------

JobStatus = str  # "queued" | "running" | "done" | "failed" | "cancelled"


@dataclass
class Job:
    id: str
    kind: str       # "ktx" | "coupang"
    params: dict[str, Any]
    status: JobStatus = "queued"
    created_at: datetime = field(default_factory=datetime.utcnow)
    result: Any = None
    error: str = ""
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: Optional[asyncio.Task] = None


# Notifier signature: async def(text, *, photo_path=None, chat_id=None)
Notifier = Callable[..., Awaitable[None]]


class JobManager:
    def __init__(self, notify: Notifier):
        self.jobs: dict[str, Job] = {}
        self.notify = notify

    # ---- Public API used by the Telegram handler ------------------------
    def submit(self, kind: str, params: dict[str, Any]) -> Job:
        from .ktx.booker import run_ktx_job  # local import to avoid cycles
        from .coupang.ranker import run_coupang_job

        jid = uuid.uuid4().hex[:8]
        job = Job(id=jid, kind=kind, params=params)
        self.jobs[jid] = job

        runner = run_ktx_job if kind == "ktx" else run_coupang_job
        job.task = asyncio.create_task(self._wrap(job, runner))
        log.info("job.submitted", id=jid, kind=kind, params=params)
        return job

    def list(self) -> list[Job]:
        return sorted(self.jobs.values(), key=lambda j: j.created_at, reverse=True)

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job or job.status not in ("queued", "running"):
            return False
        job.cancel_event.set()
        if job.task and not job.task.done():
            job.task.cancel()
        job.status = "cancelled"
        return True

    # ---- Wrapper that runs the job and updates state --------------------
    async def _wrap(self, job: Job, runner):
        job.status = "running"
        try:
            job.result = await runner(job, self.notify)
            job.status = "done"
            await self.notify(f"✅ [{job.id}] {job.kind} 완료")
        except asyncio.CancelledError:
            job.status = "cancelled"
            await self.notify(f"⏹️ [{job.id}] 취소됨")
            raise
        except Exception as exc:  # noqa: BLE001
            job.status = "failed"
            job.error = str(exc)
            log.exception("job.failed", id=job.id)
            await self.notify(f"❌ [{job.id}] 실패: {exc}")


# --- Conversation loop -----------------------------------------------------

class Orchestrator:
    """Multi-turn Claude conversation that decides what to do."""

    def __init__(self, manager: JobManager):
        self.manager = manager
        self.history: list[dict[str, Any]] = []

    async def handle(self, user_text: str) -> str:
        self.history.append({"role": "user", "content": user_text})
        for _ in range(6):  # safety cap on tool-use turns
            resp = await chat(
                system=ORCHESTRATOR_SYSTEM,
                messages=self.history,
                tools=TOOLS,
                model=ENV.orchestrator_model,
            )
            self.history.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason != "tool_use":
                # final text answer
                texts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
                return "\n".join(texts).strip() or "(빈 응답)"

            # Execute every tool call requested in this turn.
            tool_results = []
            for block in resp.content:
                if getattr(block, "type", "") != "tool_use":
                    continue
                out = await self._dispatch(block.name, block.input or {})
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(out, ensure_ascii=False, default=str),
                    }
                )
            self.history.append({"role": "user", "content": tool_results})

        return "복잡한 요청입니다. 더 명확하게 알려주세요."

    async def _dispatch(self, name: str, args: dict[str, Any]) -> Any:
        log.info("tool.dispatch", name=name, args=args)
        if name == "book_ktx":
            job = self.manager.submit("ktx", args)
            return {"job_id": job.id, "status": job.status}
        if name == "recommend_coupang":
            job = self.manager.submit("coupang", args)
            return {"job_id": job.id, "status": job.status}
        if name == "list_jobs":
            return [
                {"id": j.id, "kind": j.kind, "status": j.status, "params": j.params}
                for j in self.manager.list()
            ]
        if name == "cancel_job":
            ok = self.manager.cancel(args.get("job_id", ""))
            return {"cancelled": ok}
        return {"error": f"unknown tool: {name}"}
