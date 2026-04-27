"""
Entry point.

  python -m src.main

Starts the persistent stealth browser, then runs the Telegram bot forever.
The Telegram bot exposes the Claude-powered orchestrator and the job manager;
each job (KTX booking, Coupang ranking) runs concurrently in asyncio tasks.
"""

from __future__ import annotations

import asyncio
import signal

from .browser.stealth import POOL
from .notify.telegram import TelegramService
from .utils.log import get_logger, setup_logging


async def amain() -> None:
    setup_logging("INFO")
    log = get_logger(__name__)

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
    await stop.wait()
    runner.cancel()
    await POOL.stop()


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
