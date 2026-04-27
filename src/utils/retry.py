from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, TypeVar

from .log import get_logger

log = get_logger(__name__)

T = TypeVar("T")


class StopPolling(Exception):
    """Raise inside a poll target to terminate the loop with success."""

    def __init__(self, value):
        self.value = value


@dataclass
class BackoffPolicy:
    base: float = 8.0
    cap: float = 90.0
    jitter: float = 5.0
    factor: float = 1.4    # gentle exponential — we want to *look human*

    def delay(self, attempt: int) -> float:
        raw = min(self.cap, self.base * (self.factor ** max(0, attempt - 1)))
        return raw + random.uniform(0, self.jitter)


async def infinite_poll(
    target: Callable[[], Awaitable[Optional[T]]],
    policy: BackoffPolicy,
    deadline_sec: Optional[float] = None,
    on_attempt: Optional[Callable[[int, float], Awaitable[None]]] = None,
    cancel_event: Optional[asyncio.Event] = None,
) -> T:
    """
    Poll `target` until:
      - it returns a non-None value (success), OR
      - it raises StopPolling(value) (success with value), OR
      - deadline_sec elapses (raises TimeoutError), OR
      - cancel_event is set (raises asyncio.CancelledError).

    On other exceptions, logs and continues with backoff.
    """
    started = time.monotonic()
    attempt = 0
    while True:
        attempt += 1
        if cancel_event and cancel_event.is_set():
            raise asyncio.CancelledError("polling cancelled")
        if deadline_sec is not None and time.monotonic() - started > deadline_sec:
            raise TimeoutError(f"infinite_poll exceeded {deadline_sec}s")

        try:
            result = await target()
            if result is not None:
                return result
        except StopPolling as ok:
            return ok.value
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("poll.attempt_failed", attempt=attempt, err=str(exc))

        delay = policy.delay(attempt)
        if on_attempt:
            await on_attempt(attempt, delay)

        # Sleep but stay responsive to cancellation
        try:
            if cancel_event:
                await asyncio.wait_for(cancel_event.wait(), timeout=delay)
                raise asyncio.CancelledError("polling cancelled")
            else:
                await asyncio.sleep(delay)
        except asyncio.TimeoutError:
            continue
