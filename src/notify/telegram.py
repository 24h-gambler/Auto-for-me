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
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
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
        self.app.add_handler(CommandHandler("shutdown", self.cmd_shutdown))
        self.app.add_handler(CommandHandler("reboot", self.cmd_reboot))
        # 짧은 별칭 — 핸드폰에서 빠르게 칠 수 있게.
        self.app.add_handler(CommandHandler("go", self.cmd_refresh))     # /refresh 와 동일
        self.app.add_handler(CommandHandler("off", self.cmd_shutdown))   # /shutdown 과 동일
        self.app.add_handler(CommandHandler("stop", self.cmd_stop))      # 모든 작업 취소
        self.app.add_handler(CommandHandler("status", self.cmd_jobs))    # /jobs 와 동일
        self.app.add_handler(CallbackQueryHandler(self.cb_refresh, pattern="^rf:"))
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
            "🚄 자주 쓰는 명령 (짧은 별칭)\n"
            "  /go        ← 예매 시작 (메뉴 띄움)\n"
            "  /status    ← 진행 상황 보기\n"
            "  /stop      ← 모든 작업 즉시 취소\n"
            "  /off       ← PC 종료 (60초 뒤)\n"
            "\n"
            "🚄 자세한 명령\n"
            "  /refresh             ← /go 와 동일, 메뉴\n"
            "  /refresh 1           ← 더보기 1회 (좌석만, 초고속, 기본값)\n"
            "  /refresh 간절 2      ← 입석+좌석 + 더보기 2\n"
            "  /jobs                ← 작업 목록 (= /status)\n"
            "  /cancel <id>         ← 특정 작업만 취소\n"
            "  /shutdown [N]        ← N초 뒤 PC 종료\n"
            "  /reboot   [N]        ← N초 뒤 PC 재시작\n"
            "\n"
            "  ※ /go 메뉴 버튼:\n"
            "    [⚡ 기본]  [🔍 +1]  [📜 +2]  [📚 +3]\n"
            "    [🙏 간절합니다 / +1 / +2 / +3]\n"
            "    [🐢 일반 속도]\n"
            "  ※ 기본 = 초고속(≈0.5초) + 좌석만\n"
            "  ※ 진행 알림은 15분마다, 잡히는 즉시 별도 알림\n"
            "\n"
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
            "  /shutdown [N]    PC N초 뒤 종료 (기본 60)\n"
            "  /reboot   [N]    PC N초 뒤 재시작 (기본 30)\n"
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
            await update.message.reply_text("사용법: /cancel <job_id>\n모든 작업 취소: /stop")
            return
        ok = self.manager.cancel(ctx.args[0])
        await update.message.reply_text("취소됨." if ok else "해당 작업 없음 또는 이미 종료.")

    async def cmd_stop(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/stop — 진행 중 / 큐 대기 중 모든 작업 일괄 취소."""
        if not _is_authorized(update.effective_chat.id):
            return
        cancelled = []
        for j in list(self.manager.jobs.values()):
            if j.status in ("queued", "running"):
                if self.manager.cancel(j.id):
                    cancelled.append(j.id)
        if cancelled:
            await update.message.reply_text(
                f"⏹️ {len(cancelled)}개 작업 취소됨: {', '.join(cancelled)}"
            )
        else:
            await update.message.reply_text("진행 중 작업 없음.")

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

        기본값: 초고속 + 좌석만.
        입석+좌석 까지 잡으려면 메뉴에서 '🙏 간절합니다' 를 누르거나
        인자에 '간절' / '입석' 단어를 넣어주세요.
        """
        if not _is_authorized(update.effective_chat.id):
            return
        a = ctx.args or []

        # 인자 없으면 메뉴 띄움.
        if not a:
            self._default_chat_id = update.effective_chat.id
            # callback_data 형식: rf:<aggr>:<expand>:<seat>
            #   aggr: 1=초고속(기본) / 0=일반
            #   expand: 0~3
            #   seat: o=좌석만(기본) / s=좌석+입석
            kb = [
                [
                    InlineKeyboardButton("⚡ 기본", callback_data="rf:1:0:o"),
                    InlineKeyboardButton("🔍 +더보기 1", callback_data="rf:1:1:o"),
                    InlineKeyboardButton("📜 +더보기 2", callback_data="rf:1:2:o"),
                    InlineKeyboardButton("📚 +더보기 3", callback_data="rf:1:3:o"),
                ],
                [
                    InlineKeyboardButton("🙏 간절합니다", callback_data="rf:1:0:s"),
                    InlineKeyboardButton("🙏 간절 +1", callback_data="rf:1:1:s"),
                    InlineKeyboardButton("🙏 간절 +2", callback_data="rf:1:2:s"),
                    InlineKeyboardButton("🙏 간절 +3", callback_data="rf:1:3:s"),
                ],
                [
                    InlineKeyboardButton("🐢 일반 속도 (탐지 걱정 시)", callback_data="rf:0:0:o"),
                ],
            ]
            await update.message.reply_text(
                "어떤 모드로 시작할까요? (기본 = 초고속 + 좌석만)\n"
                "  · 기본: F5 따닥 + 좌석만 잡음 (입석+좌석 무시)\n"
                "  · +더보기 N: F5 후 더보기 N번 자동 펼치고 스캔 (시간대 확장)\n"
                "  · 🙏 간절합니다: 좌석 다 놓쳐도 입석+좌석 잡음 (절박 시)\n"
                "  · 🐢 일반 속도: 탐지 걱정될 때 (≈1초 간격)",
                reply_markup=InlineKeyboardMarkup(kb),
            )
            return

        # 인자 직접 파싱 — 기본 초고속 + 좌석만.
        # '간절' 또는 '입석' 단어 있으면 입석+좌석 포함.
        # '보통' 또는 '느림' 단어 있으면 일반 속도.
        aggressive = not any(t in a for t in ("보통", "느림", "느리게"))
        allow_standing = any(t in a for t in ("간절", "절박", "입석"))
        expand_count = 0
        for tok in a:
            try:
                n = int(tok)
                expand_count = max(0, min(3, n))
                break
            except ValueError:
                continue
        await self._start_refresh(
            chat_id=update.effective_chat.id,
            reply_to=update.message.reply_text,
            aggressive=aggressive,
            allow_standing=allow_standing,
            expand_count=expand_count,
        )

    async def cb_refresh(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """인라인 버튼 콜백 — callback_data 형식: 'rf:<aggr>:<expand>:<seat>'
            aggr: 0|1, expand: 0~3, seat: 's' (좌석+입석) | 'o' (좌석만)"""
        q = update.callback_query
        if not q or not _is_authorized(q.message.chat_id):
            return
        await q.answer()
        try:
            _, aggr, exp, seat = q.data.split(":")
            aggressive = bool(int(aggr))
            expand_count = max(0, min(3, int(exp)))
            allow_standing = (seat == "s")
        except Exception:
            await q.edit_message_text("⚠️ 옵션 파싱 오류.")
            return
        await self._start_refresh(
            chat_id=q.message.chat_id,
            reply_to=q.edit_message_text,
            aggressive=aggressive,
            allow_standing=allow_standing,
            expand_count=expand_count,
        )

    async def _start_refresh(
        self, *, chat_id: int, reply_to, aggressive: bool, allow_standing: bool, expand_count: int
    ) -> None:
        # 이미 돌고 있는 ktx 작업이 있으면 먼저 취소 — 같은 페이지를 두고
        # 두 작업이 충돌하지 않게.
        cancelled_ids = []
        for j in list(self.manager.jobs.values()):
            if j.kind == "ktx" and j.status in ("queued", "running"):
                if self.manager.cancel(j.id):
                    cancelled_ids.append(j.id)

        params = {
            "seat_class_strategy": "refresh",
            "aggressive": aggressive,
            "allow_standing": allow_standing,
            "expand_count": expand_count,
        }
        self._default_chat_id = chat_id
        job = self.manager.submit("ktx", params, chat_id=chat_id)
        notice = ""
        if cancelled_ids:
            notice = f"\n   (이전 작업 {', '.join(cancelled_ids)} 취소됨)"
        await reply_to(
            f"🔁 [{job.id}] 새로고침 모드 시작\n"
            f"   속도: {'🔥초고속' if aggressive else '🐢보통'}\n"
            f"   범위: {'좌석만' if not allow_standing else '좌석+입석'}\n"
            f"   더보기: {expand_count}회/회차{notice}\n"
            f"진행 알림은 15분마다. 잡히는 즉시 별도 알림."
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

    # ---- Remote PC control (외출 시 핸드폰으로 PC 끄기) -----------------
    async def cmd_shutdown(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/shutdown — PC 전원 끄기 (Windows/macOS/Linux)."""
        if not _is_authorized(update.effective_chat.id):
            return
        delay = 60
        if ctx.args:
            try:
                delay = int(ctx.args[0])
            except ValueError:
                pass
        await update.message.reply_text(
            f"🛑 {delay}초 뒤 PC 종료. 취소하려면 OS 별 cancel 명령:\n"
            f"  Windows: shutdown /a\n"
            f"  macOS:   sudo killall shutdown"
        )
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(["shutdown", "/s", "/t", str(delay)])
            elif sys.platform == "darwin":
                # macOS: needs sudoers entry. mins must be >= 1.
                mins = max(1, delay // 60)
                subprocess.Popen(["sudo", "shutdown", "-h", f"+{mins}"])
            else:
                subprocess.Popen(["shutdown", "-h", f"+{max(1, delay // 60)}"])
        except Exception as exc:  # noqa: BLE001
            await update.message.reply_text(f"⚠️ 종료 명령 실패: {exc}")

    async def cmd_reboot(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/reboot — PC 재시작."""
        if not _is_authorized(update.effective_chat.id):
            return
        delay = 30
        if ctx.args:
            try:
                delay = int(ctx.args[0])
            except ValueError:
                pass
        await update.message.reply_text(f"🔁 {delay}초 뒤 PC 재시작.")
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(["shutdown", "/r", "/t", str(delay)])
            elif sys.platform == "darwin":
                mins = max(1, delay // 60)
                subprocess.Popen(["sudo", "shutdown", "-r", f"+{mins}"])
            else:
                subprocess.Popen(["shutdown", "-r", f"+{max(1, delay // 60)}"])
        except Exception as exc:  # noqa: BLE001
            await update.message.reply_text(f"⚠️ 재시작 명령 실패: {exc}")

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
