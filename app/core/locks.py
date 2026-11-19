"""Per-key asyncio locks for serializing concurrent same-entity requests.

Lives in core so services/api stay free of try/except: callers only use
`async with transfer_lock(key)`. The database UNIQUE constraint remains the
backstop for multi-process races; in-process serialization just makes the
common path deterministic and deadlock-free.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

_locks: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Lock]] = {}


@asynccontextmanager
async def transfer_lock(key: str) -> AsyncIterator[None]:
    loop = asyncio.get_running_loop()
    entry = _locks.get(key)
    if entry is None or entry[0] is not loop or entry[0].is_closed():
        entry = (loop, asyncio.Lock())
        _locks[key] = entry
    _, lock = entry
    await lock.acquire()
    try:
        yield
    finally:
        lock.release()
        if _locks.get(key, (None, None))[1] is lock and not lock.locked():
            _locks.pop(key, None)
