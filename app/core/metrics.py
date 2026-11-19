import logging

logger = logging.getLogger("transfer-core.metrics")

_counters: dict[str, int] = {}


def inc(name: str, value: int = 1) -> int:
    _counters[name] = _counters.get(name, 0) + value
    return _counters[name]


def get(name: str) -> int:
    return _counters.get(name, 0)


def snapshot() -> dict[str, int]:
    return dict(_counters)


def reset() -> None:
    _counters.clear()
