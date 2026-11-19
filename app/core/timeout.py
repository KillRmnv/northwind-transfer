"""Timeout helper without try/except at call sites.

`asyncio.wait` reports completion via sets instead of raising, so callers
never write their own timeout handling. Cancellation of the inner task is
awaited (and swallowed) here, keeping the caller's session usable after
an explicit rollback in `on_timeout`.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any


async def run_with_timeout(
    coro_factory: Callable[[], Awaitable[Any]],
    timeout_: timedelta,
    on_timeout: Callable[[], Awaitable[Any]],
) -> Any:
    task = asyncio.ensure_future(coro_factory())
    done, pending = await asyncio.wait({task}, timeout=timeout_.total_seconds())
    if done:
        return task.result()
    for unfinished in pending:
        unfinished.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    return await on_timeout()
