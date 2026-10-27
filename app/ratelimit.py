"""Простейшее ограничение частоты запросов в памяти процесса.

Для демо этого достаточно. На реальном проекте с несколькими процессами
используйте общее хранилище (например, Redis).
"""
import time
from collections import defaultdict, deque

_hits: dict[str, deque] = defaultdict(deque)
_MAX_KEYS = 5000     # предел числа запомненных ключей (адресов), чтобы память не росла бесконечно
_RETENTION = 3600    # через столько секунд после последнего обращения ключ можно забыть


def _now() -> float:
    return time.monotonic()


def _prune(queue: deque, now: float, window: int) -> None:
    while queue and now - queue[0] > window:
        queue.popleft()


def _cleanup(now: float) -> None:
    """Когда ключей накопилось много, выбрасываем тех, кто давно не обращался."""
    if len(_hits) <= _MAX_KEYS:
        return
    stale = [key for key, queue in _hits.items() if not queue or now - queue[-1] > _RETENTION]
    for key in stale:
        del _hits[key]


def count(key: str, window: int = 3600) -> int:
    """Сколько обращений с этим ключом за последние `window` секунд (ничего не записывает)."""
    queue = _hits.get(key)
    if not queue:
        return 0
    _prune(queue, _now(), window)
    return len(queue)


def hit(key: str) -> None:
    """Записать обращение."""
    now = _now()
    _cleanup(now)
    _hits[key].append(now)


def allow(key: str, limit: int = 5, window: int = 3600) -> bool:
    """True, если с этого ключа можно принять ещё одно обращение (и записывает его)."""
    if count(key, window) >= limit:
        return False
    hit(key)
    return True


def reset() -> None:
    _hits.clear()
