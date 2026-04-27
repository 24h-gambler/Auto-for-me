"""
Entry point.

  python -m src.main

Lifecycle:
  1. init SQLite (jobs + watches tables)
  2. start the persistent stealth Chromium
  3. start the Telegram service
  4. resume any jobs that were 'queued' / 'running' at last shutdown
  5. block forever, gracefully stop on SIGINT / SIGTERM
"""

from __future__ import annotations

import asyncio
import signal

from . import state
from .browser.stealth import POOL
from .notify.telegram import TelegramService
from .utils.log import get_logger, setup_logging


async def amain() -> None:
    setup_logging("INFO")
    log = get_logger(__name__)

    state.init_db()
    await POOL.start()

    svc = TelegramService()

    stop = asyncio.Event()

    def _stop(*_):
        log.info("shutdown.requested")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            pass  # windows

    runner = asyncio.create_task(svc.run())

    # Resume jobs that didn't finish before last shutdown.
    n = await svc.manager.resume_pending()
    if n:
        log.info("resumed.jobs", count=n)

    await stop.wait()
    runner.cancel()
    await POOL.stop()


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
