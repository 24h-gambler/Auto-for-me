"""
Telegram interface.

Responsibilities
----------------
- Authenticate the chat (whitelist).
- Forward free-form messages to the Orchestrator (Claude tool-use agent).
- Slash commands for direct/explicit control:
    /ktx, /coupang, /jobs, /cancel <id>, /captcha <code>
- Relay screenshots when the booker requests captcha/2FA from the user.
- Provide a `Notifier` callable that any module can use to push messages.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..config import ENV
from ..orchestrator import JobManager, Orchestrator
from .. import state
from ..utils.log import get_logger

log = get_logger(__name__)


# A single global queue from anyone who needs to hand a captcha string back.
CAPTCHA_QUEUE: asyncio.Queue[str] = asyncio.Queue()


def _is_authorized(chat_id: int) -> bool:
    allow = ENV.allowed_chat_ids
    return not allow or chat_id in allow


class TelegramService:
    def __init__(self) -> None:
        self.app: Application = (
            Application.builder().token(ENV.telegram_bot_token).build()
        )
        self.manager = JobManager(self.notify)
        self.orch = Orchestrator(self.manager)
        self._default_chat_id: Optional[int] = None

        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        self.app.add_handler(CommandHandler("jobs", self.cmd_jobs))
        self.app.add_handler(CommandHandler("cancel", self.cmd_cancel))
        self.app.add_handler(CommandHandler("captcha", self.cmd_captcha))
        self.app.add_handler(CommandHandler("ktx", self.cmd_ktx))
        self.app.add_handler(CommandHandler("book", self.cmd_book))
        self.app.add_handler(CommandHandler("refresh", self.cmd_refresh))
        self.app.add_handler(CommandHandler("coupang", self.cmd_coupang))
        self.app.add_handler(CommandHandler("watch", self.cmd_watch))
        self.app.add_handler(CommandHandler("unwatch", self.cmd_unwatch))
        self.app.add_handler(CommandHandler("watches", self.cmd_watches))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))

    # ---- Notifier (used by orchestrator/booker/ranker) ------------------
    async def notify(self, text: str, *, photo_path: str | None = None, chat_id: int | None = None) -> None:
        target = chat_id or self._default_chat_id
        if target is None:
            log.warning("notify.no_chat_id", text=text[:80])
            return
        # 기본은 plain text — 알림 텍스트에는 ParseMode 불필요. HTML/MarkDown
        # 파싱을 켜놓으면 메시지 안의 '<' 같은 평범한 글자에서 Telegram 이 거부합니다.
        try:
            if photo_path and Path(photo_path).exists():
                with open(photo_path, "rb") as f:
                    await self.app.bot.send_photo(target, photo=f, caption=text[:1024])
            else:
                await self.app.bot.send_message(target, text)
        except Exception as exc:  # noqa: BLE001
            log.error("notify.failed", err=str(exc))

    # ---- Handlers --------------------------------------------------------
    async def cmd_start(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        if not chat or not _is_authorized(chat.id):
            return
        self._default_chat_id = chat.id
        await update.message.reply_text(
            "👋 Auto-for-me 가동.\n"
            "자유 채팅하거나 /help 로 명령어를 보세요.\n"
            f"chat_id: <code>{chat.id}</code> (.env 의 TELEGRAM_ALLOWED_CHAT_IDS 에 추가하세요)",
            parse_mode=ParseMode.HTML,
        )

    async def cmd_help(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "⚡ HYPER 새로고침 모드 (사용자가 직접 로그인+검색 → 봇이 F5 따닥)\n"
            "  /refresh             ← 좌석+입석 둘 다 (≈1초마다 F5)\n"
            "  /refresh !           ← 초고속 (≈0.5초마다 F5, 탐지 위험 ↑)\n"
            "  /refresh 좌석        ← 좌석만, 입석 무시\n"
            "  /refresh ! 좌석      ← 초고속 + 좌석만\n"
            "\n"
            "  → 잡히면: 셀 클릭 + 예매 버튼 단일 JS 호출로 따닥 → 알림\n"
            "\n"
            "🚄 자동 모드 (봇이 처음부터 다 함, 탐지 위험)\n"
            "  /book 서울 부산 2026-05-10 09:00 120     ← 빠른 자동 폴링\n"
            "  /ktx  서울 부산 2026-05-10 09:00 120 !   ← /book 과 동일\n"
            "  /ktx  서울 부산 2026-05-10 09:00 120     ← 보통 자동 폴링\n"
            "  마지막 숫자는 ±분(시간 폭)\n"
            "\n"
            "👀 클라우드 감시 (PC 꺼져있을 때)\n"
            "  /watch 서울 부산 2026-05-10 09:00 120\n"
            "  /watches  /unwatch <id>\n"
            "\n"
            "🛠️ 운영\n"
            "  /jobs        진행/완료 작업 목록\n"
            "  /cancel <id> 작업 취소\n"
            "  /captcha <code>  봇이 요청하면 캡차 입력\n"
            "\n"
            "🛒 쿠팡 (Anthropic API 키 필요)\n"
            "  /coupang 무선마우스 50000 사무용\n"
            "\n"
            "💬 자유채팅도 됩니다 (API 키 있을 때만)."
        )

    async def cmd_jobs(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_authorized(update.effective_chat.id):
            return
        jobs = self.manager.list()
        if not jobs:
            await update.message.reply_text("작업 없음.")
            return
        lines = [
            f"[{j.id}] {j.kind} — {j.status} ({j.created_at:%H:%M:%S})"
            for j in jobs
        ]
        await update.message.reply_text("\n".join(lines))

    async def cmd_cancel(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_authorized(update.effective_chat.id):
            return
        if not ctx.args:
            await update.message.reply_text("사용법: /cancel <job_id>")
            return
        ok = self.manager.cancel(ctx.args[0])
        await update.message.reply_text("취소됨." if ok else "해당 작업 없음 또는 이미 종료.")

    async def cmd_captcha(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_authorized(update.effective_chat.id):
            return
        if not ctx.args:
            await update.message.reply_text("사용법: /captcha <코드>")
            return
        await CAPTCHA_QUEUE.put(" ".join(ctx.args))
        await update.message.reply_text("입력 수신. 진행합니다.")

    async def cmd_ktx(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_authorized(update.effective_chat.id):
            return
        # /ktx <origin> <dest> <YYYY-MM-DD> <HH:MM> [window_min] [! 빠른 폴링]
        a = ctx.args
        if len(a) < 4:
            await update.message.reply_text(
                "사용법: /ktx 서울 부산 2026-05-10 09:00 120\n"
                "         (마지막에 ! 붙이면 빠른 폴링: /ktx 서울 부산 2026-05-10 09:00 120 !)\n"
                "또는: /book 서울 부산 2026-05-10 09:00 120  (= 빠른 폴링 단축키)"
            )
            return
        aggressive = a[-1] == "!"
        if aggressive:
            a = a[:-1]
        params = {
            "origin": a[0],
            "destination": a[1],
            "date": a[2],
            "time": a[3],
            "window_minutes": int(a[4]) if len(a) > 4 else 120,
            "seat_class_strategy": "any",
            "aggressive": aggressive,
        }
        self._default_chat_id = update.effective_chat.id
        job = self.manager.submit("ktx", params, chat_id=update.effective_chat.id)
        await update.message.reply_text(
            f"등록됨 [{job.id}] · {'🔥 빠른 폴링' if aggressive else '🐢 일반 폴링'}"
        )

    async def cmd_refresh(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/refresh — HYPER 새로고침 모드.

        사용자가 봇 chromium 창에서 직접 로그인+검색까지 끝낸 페이지를
        받아, 봇이 F5 + 매진 아닌 셀 + 예매 버튼을 단일 JS 호출로 따닥
        클릭한다. 사람 손 속도와 동등.

        플래그 (한 줄 끝에 공백으로 구분, 순서 무관):
          !       초고속 (≈0.3~0.8초마다 F5). 기본은 ≈0.8~2초.
          좌석    좌석만 잡음. (입석+좌석 무시)

        예시:
          /refresh                  ← 좌석 + 입석+좌석 (기본 속도)
          /refresh !                ← 초고속
          /refresh 좌석             ← 좌석만
          /refresh ! 좌석           ← 초고속 + 좌석만
        """
        if not _is_authorized(update.effective_chat.id):
            return
        a = ctx.args or []
        aggressive = "!" in a
        # '좌석' 만 명시되면 입석+좌석 잡지 않음. 기본은 둘 다 잡음.
        if "좌석" in a and "입석" not in a:
            allow_standing = False
        else:
            allow_standing = True
        params = {
            "seat_class_strategy": "refresh",
            "aggressive": bool(aggressive),
            "allow_standing": allow_standing,
        }
        self._default_chat_id = update.effective_chat.id
        job = self.manager.submit("ktx", params, chat_id=update.effective_chat.id)
        await update.message.reply_text(
            f"🔁 [{job.id}] 새로고침 모드 시작 "
            f"({'🔥빠름' if aggressive else '🐢보통'}, "
            f"{'좌석만' if not allow_standing else '좌석+입석'})\n"
            f"봇 chromium 창에서 검색 결과 페이지가 떠 있어야 합니다.\n"
            f"매진 아닌 좌석 발견 시 즉시 클릭 → 예매 → 결제 페이지로."
        )

    async def cmd_book(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/book 서울 부산 2026-05-10 09:00 120
        = /ktx 와 동일하지만 항상 빠른 폴링 + 일반/특실 가리지 않고 잡힘 즉시 결제."""
        if not _is_authorized(update.effective_chat.id):
            return
        a = ctx.args
        if len(a) < 4:
            await update.message.reply_text("사용법: /book 서울 부산 2026-05-10 09:00 120")
            return
        params = {
            "origin": a[0],
            "destination": a[1],
            "date": a[2],
            "time": a[3],
            "window_minutes": int(a[4]) if len(a) > 4 else 120,
            "seat_class_strategy": "any",
            "aggressive": True,
        }
        self._default_chat_id = update.effective_chat.id
        job = self.manager.submit("ktx", params, chat_id=update.effective_chat.id)
        await update.message.reply_text(f"🔥 [{job.id}] 빠른 폴링 시작 — 잡히면 즉시 알림.")

    async def cmd_coupang(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_authorized(update.effective_chat.id):
            return
        if not ctx.args:
            await update.message.reply_text("사용법: /coupang <쿼리> [상한가] [용도]")
            return
        params = {
            "query": ctx.args[0],
            "max_price_krw": int(ctx.args[1]) if len(ctx.args) > 1 and ctx.args[1].isdigit() else 0,
            "intent": " ".join(ctx.args[2:]) if len(ctx.args) > 2 else "",
        }
        self._default_chat_id = update.effective_chat.id
        job = self.manager.submit("coupang", params)
        await update.message.reply_text(f"등록됨 [{job.id}]")

    async def on_text(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        if not chat or not _is_authorized(chat.id):
            return
        self._default_chat_id = chat.id
        text = update.message.text or ""
        try:
            reply = await self.orch.handle(text, chat_id=chat.id)
        except Exception as exc:  # noqa: BLE001
            log.exception("orch.failed")
            reply = f"오류: {exc}"
        await update.message.reply_text(reply)

    # ---- Watch list (cloud cron uses this) ------------------------------
    async def cmd_watch(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_authorized(update.effective_chat.id):
            return
        a = ctx.args
        if len(a) < 4:
            await update.message.reply_text(
                "사용법: /watch 서울 부산 2026-05-10 09:00 [window=120]\n"
                "PC 가 꺼져있어도 GitHub Actions 가 5~10분마다 좌석을 감시합니다."
            )
            return
        wid = f"w-{int(__import__('time').time())}"
        query = {
            "origin": a[0],
            "destination": a[1],
            "date": a[2],
            "time": a[3],
            "window_minutes": int(a[4]) if len(a) > 4 else 120,
        }
        state.add_watch(wid, query, update.effective_chat.id)
        await update.message.reply_text(
            f"👀 [{wid}] 감시 시작\n"
            f"{query['origin']}→{query['destination']} {query['date']} {query['time']} ±{query['window_minutes']}분\n"
            "좌석이 감지되면 텔레그램으로 알림이 옵니다.\n"
            "끝나면 `/unwatch {wid}` 로 중지하세요."
        )

    async def cmd_unwatch(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_authorized(update.effective_chat.id):
            return
        if not ctx.args:
            await update.message.reply_text("사용법: /unwatch <id>")
            return
        ok = state.remove_watch(ctx.args[0])
        await update.message.reply_text("중지됨." if ok else "해당 id 없음.")

    async def cmd_watches(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_authorized(update.effective_chat.id):
            return
        ws = state.list_watches()
        if not ws:
            await update.message.reply_text("감시 중 없음.")
            return
        lines = []
        for w in ws:
            q = w["query"]
            lines.append(
                f"[{w['id']}] {q['origin']}→{q['destination']} {q['date']} {q['time']} ±{q['window_minutes']}m"
            )
        await update.message.reply_text("\n".join(lines))

    # ---- Lifecycle -------------------------------------------------------
    async def run(self) -> None:
        log.info("telegram.starting")
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        try:
            # park forever
            stop = asyncio.Event()
            await stop.wait()
        finally:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
