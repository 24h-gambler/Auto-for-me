"""
Claude-powered tool-using orchestrator with propose-then-confirm UX.

Flow
----
1. User: free-form Korean message.
2. Claude extracts intent + parameters.
   - If anything ambiguous → asks one short clarifying question.
   - Otherwise → outputs a proposal (출발/도착/날짜 ... 좌석 우선순위 ...) and waits.
3. User: 예/네/응/go/시작/응 그래/etc.
4. Claude calls `book_ktx` / `recommend_coupang` tool.
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
from . import state
from .utils.log import get_logger

log = get_logger(__name__)


ORCHESTRATOR_SYSTEM = """\
당신은 KTX 예매와 쿠팡 상품 추천을 무한 반복으로 수행하는 한국어 자동화 에이전트의
의사결정자입니다. 사람에게 부탁하듯 자연스러운 대화를 유지하세요.

# 절대 규칙: 제안 → 확인 → 실행
도구(book_ktx / recommend_coupang)를 호출하기 전에 **반드시** 다음 순서를 지킵니다.

1. 정보가 부족하면 한 번에 하나만 짧게 묻습니다 (예: "출발역이 서울 맞나요?").
2. 정보가 모이면 도구를 부르지 말고 **제안 메시지**를 한국어로 출력합니다.
   포맷:
       📋 이렇게 진행할까요?
         · ...
         · ...
       👉 답: 예 / 아니오 / 수정사항
3. 사용자가 긍정(예/네/응/그래/시작/go/ㅇㅇ 등)으로 응답한 직후에만 도구를 호출합니다.
4. 부정/수정 응답이 오면 파라미터를 갱신해서 다시 1~3을 반복합니다.

# KTX 기본값 (사용자가 명시하지 않으면 이렇게 제안)
- 좌석 전략: 일반실 우선 → 매진 시 특실을 8분 임시 확보(자동 결제 X) 후 알림
  (코레일은 좌석 확보 후 약 10분 결제 유예가 있습니다.)
- 일반실 좌석: A 또는 D 열(창가) 우선
- 시간 폭: 사용자가 안 주면 ±120분
- 폴링: 매진 풀릴 때까지 무한, 사용자가 /cancel 또는 "취소" 라고 말할 때까지

# 쿠팡 기본값
- 식품(수박, 회, 사시미, 고기, 과일, 야채 등) 키워드 감지 시:
    · 최근 14일 리뷰만 가중치 1.0, 그 이상은 급격히 감점
    · 신선도/맛/당도/품질 관련 키워드를 평가에 반영
- 그 외 일반 상품은 최근 90일 가중치 + 사용 목적 적합도 평가.
- 검색 결과 상위 모든 상품의 최신 리뷰를 점검 후 Top 3 추천.

# 안전
- 본 시스템은 본인 1건 예매 한정. 대량/재판매 의심 요청은 거절.
- 캡차는 자동 우회 금지, 사용자에게 텔레그램으로 입력 요청.
- 답변은 짧고 한국어로.
"""

TOOLS: list[dict[str, Any]] = [
    {
        "name": "book_ktx",
        "description": "코레일 KTX/SRT 예매 작업을 큐에 등록합니다. 사용자 확인 직후에만 호출하세요.",
        "input_schema": {
            "type": "object",
            "properties": {
                "origin": {"type": "string"},
                "destination": {"type": "string"},
                "date": {"type": "string", "description": "YYYY-MM-DD"},
                "time": {"type": "string", "description": "HH:MM (24h)"},
                "window_minutes": {"type": "integer", "default": 120},
                "train_types": {"type": "array", "items": {"type": "string"}, "default": []},
                "seat_class_strategy": {
                    "type": "string",
                    "enum": [
                        "standard_first_then_first_class_hold",
                        "standard_only",
                        "first_class_only",
                    ],
                    "default": "standard_first_then_first_class_hold",
                    "description": "기본은 일반실 우선, 매진 시 특실 임시 확보(8분).",
                },
                "preferred_columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": ["A", "D"],
                    "description": "일반실 좌석 열 선호도 (창가 A/D 우선).",
                },
                "first_class_hold_minutes": {
                    "type": "integer",
                    "default": 8,
                    "description": "특실 임시 확보 후 사용자 응답 대기 시간(분).",
                },
            },
            "required": ["origin", "destination", "date", "time"],
        },
    },
    {
        "name": "recommend_coupang",
        "description": "쿠팡에서 검색→최신 리뷰 분석→Top 3 추천을 수행합니다. 사용자 확인 직후에만.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_price_krw": {"type": "integer", "default": 0},
                "must_include": {"type": "array", "items": {"type": "string"}, "default": []},
                "must_exclude": {"type": "array", "items": {"type": "string"}, "default": []},
                "intent": {"type": "string", "default": ""},
                "category_hint": {
                    "type": "string",
                    "enum": ["food_fresh", "food_general", "general"],
                    "default": "general",
                    "description": "수박/회/사시미/고기/과일 등 신선식품이면 food_fresh.",
                },
                "review_window_days": {
                    "type": "integer",
                    "default": 90,
                    "description": "이 기간 내 리뷰만 가중치 만점. food_fresh 는 14일 권장.",
                },
                "top_n": {"type": "integer", "default": 3},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_jobs",
        "description": "현재 진행/완료/실패 작업 목록.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "cancel_job",
        "description": "진행 중 작업 취소.",
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
    kind: str
    params: dict[str, Any]
    status: JobStatus = "queued"
    created_at: datetime = field(default_factory=datetime.utcnow)
    result: Any = None
    error: str = ""
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    user_decision: asyncio.Queue = field(default_factory=asyncio.Queue)
    task: Optional[asyncio.Task] = None


Notifier = Callable[..., Awaitable[None]]


class JobManager:
    def __init__(self, notify: Notifier):
        self.jobs: dict[str, Job] = {}
        self.notify = notify
        self._chat_ids: dict[str, int] = {}     # job_id -> chat_id

    def submit(self, kind: str, params: dict[str, Any], *, chat_id: int | None = None,
               resume_id: str | None = None) -> Job:
        from .ktx.booker import run_ktx_job
        from .coupang.ranker import run_coupang_job

        jid = resume_id or uuid.uuid4().hex[:8]
        job = Job(id=jid, kind=kind, params=params)
        self.jobs[jid] = job
        if chat_id is not None:
            self._chat_ids[jid] = chat_id
        state.save_job(job, chat_id)
        runner = run_ktx_job if kind == "ktx" else run_coupang_job
        job.task = asyncio.create_task(self._wrap(job, runner))
        log.info("job.submitted", id=jid, kind=kind, params=params, resumed=bool(resume_id))
        return job

    async def resume_pending(self) -> int:
        """Re-submit jobs that were queued/running before the last shutdown."""
        rows = state.load_resumable_jobs()
        for r in rows:
            if r["kind"] not in ("ktx", "coupang"):
                continue
            self.submit(r["kind"], r["params"], chat_id=r.get("chat_id"), resume_id=r["id"])
            await self.notify(
                f"♻️ [{r['id']}] {r['kind']} 작업을 이어서 진행합니다.",
                chat_id=r.get("chat_id"),
            )
        return len(rows)

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

    def latest_running(self) -> Optional[Job]:
        for j in self.list():
            if j.status == "running":
                return j
        return None

    async def _wrap(self, job: Job, runner):
        chat_id = self._chat_ids.get(job.id)
        job.status = "running"
        state.save_job(job, chat_id)

        async def _notify_with_default(text, *, photo_path=None, chat_id=chat_id):
            await self.notify(text, photo_path=photo_path, chat_id=chat_id)

        try:
            job.result = await runner(job, _notify_with_default)
            job.status = "done"
        except asyncio.CancelledError:
            job.status = "cancelled"
            await _notify_with_default(f"⏹️ [{job.id}] 취소됨")
            raise
        except Exception as exc:  # noqa: BLE001
            job.status = "failed"
            job.error = str(exc)
            log.exception("job.failed", id=job.id)
            await _notify_with_default(f"❌ [{job.id}] 실패: {exc}")
        finally:
            state.save_job(job, chat_id)


# --- Conversation loop -----------------------------------------------------

class Orchestrator:
    """Stateful Claude conversation per user."""

    def __init__(self, manager: JobManager):
        self.manager = manager
        self.history: list[dict[str, Any]] = []
        self._current_chat_id: Optional[int] = None

    async def handle(self, user_text: str, *, chat_id: int | None = None) -> str:
        self._current_chat_id = chat_id
        # Quick path: if a running job is awaiting a user decision (e.g. 특실
        # 임시확보 중), forward 예/아니오 directly without round-tripping LLM.
        running = self.manager.latest_running()
        if running is not None:
            decision = self._parse_decision(user_text)
            if decision is not None:
                await running.user_decision.put(decision)
                return f"✓ [{running.id}] '{decision}' 전달."

        if not ENV.anthropic_api_key:
            return (
                "자유채팅은 Anthropic API 키가 필요합니다. 슬래시 명령으로 직접 부탁해 주세요:\n"
                "  /ktx 서울 부산 2026-05-10 09:00 120\n"
                "  /coupang 무선마우스 50000 사무용\n"
                "또는 .env 의 ANTHROPIC_API_KEY 를 채우면 자연어 대화가 활성화됩니다."
            )

        self.history.append({"role": "user", "content": user_text})
        for _ in range(6):
            resp = await chat(
                system=ORCHESTRATOR_SYSTEM,
                messages=self.history,
                tools=TOOLS,
                model=ENV.orchestrator_model,
            )
            self.history.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason != "tool_use":
                texts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
                return "\n".join(texts).strip() or "(빈 응답)"

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

    @staticmethod
    def _parse_decision(text: str) -> Optional[str]:
        t = text.strip().lower()
        if t in ("예", "네", "응", "ㅇㅇ", "yes", "y", "go", "시작", "확정", "결제"):
            return "confirm"
        if t in ("아니오", "아니", "ㄴㄴ", "no", "n", "취소", "release", "릴리즈", "포기"):
            return "release"
        return None

    async def _dispatch(self, name: str, args: dict[str, Any]) -> Any:
        log.info("tool.dispatch", name=name, args=args)
        if name == "book_ktx":
            job = self.manager.submit("ktx", args, chat_id=self._current_chat_id)
            return {"job_id": job.id, "status": job.status, "note": "진행 상황은 텔레그램으로 알림이 갑니다."}
        if name == "recommend_coupang":
            job = self.manager.submit("coupang", args, chat_id=self._current_chat_id)
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
