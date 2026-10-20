"""Простейшее ограничение частоты запросов в памяти процесса.

Для демо этого достаточно. На реальном проекте с несколькими процессами
используйте общее хранилище (например, Redis).
"""
import time
from collections import defaultdict, deque

_hits: dict[str, deque] = defaultdict(deque)


def allow(key: str, limit: int = 5, window: int = 3600) -> bool:
    """True, если с этого ключа (IP) можно принять ещё одну заявку."""
    now = time.monotonic()
    queue = _hits[key]
    while queue and now - queue[0] > window:
        queue.popleft()
    if len(queue) >= limit:
        return False
    queue.append(now)
    return True


def reset() -> None:
    _hits.clear()
