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
        try:
            if photo_path and Path(photo_path).exists():
                with open(photo_path, "rb") as f:
                    await self.app.bot.send_photo(target, photo=f, caption=text[:1024])
            else:
                await self.app.bot.send_message(target, text, parse_mode=ParseMode.HTML)
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
            "/ktx 서울 부산 2026-05-10 09:00 120  (마지막 숫자는 ±분)\n"
            "/coupang 무선마우스 50000 사무용\n"
            "/jobs        진행/완료 작업 목록\n"
            "/cancel <id> 작업 취소\n"
            "/captcha <code>  봇이 요청하면 캡차 입력\n"
            "또는 그냥 자유롭게 한국어로 말씀하세요. Claude 가 알아서 해석합니다."
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
        # /ktx <origin> <dest> <YYYY-MM-DD> <HH:MM> [window_min]
        a = ctx.args
        if len(a) < 4:
            await update.message.reply_text("사용법: /ktx 서울 부산 2026-05-10 09:00 120")
            return
        params = {
            "origin": a[0],
            "destination": a[1],
            "date": a[2],
            "time": a[3],
            "window_minutes": int(a[4]) if len(a) > 4 else 120,
        }
        self._default_chat_id = update.effective_chat.id
        job = self.manager.submit("ktx", params)
        await update.message.reply_text(f"등록됨 [{job.id}]")

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
