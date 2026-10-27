"""Тесты ограничителя частоты. FastAPI здесь не нужен."""
import pytest

from app import ratelimit


@pytest.fixture(autouse=True)
def clean():
    ratelimit.reset()
    yield
    ratelimit.reset()


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(ratelimit, "_now", lambda: now[0])
    return now


def test_allows_up_to_limit_then_blocks(clock):
    results = [ratelimit.allow("ip", limit=3, window=60) for _ in range(5)]
    assert results == [True, True, True, False, False]


def test_window_expires(clock):
    for _ in range(3):
        ratelimit.allow("ip", limit=3, window=60)
    assert not ratelimit.allow("ip", limit=3, window=60)
    clock[0] += 61
    assert ratelimit.allow("ip", limit=3, window=60)


def test_keys_are_independent(clock):
    for _ in range(3):
        ratelimit.allow("a", limit=3, window=60)
    assert not ratelimit.allow("a", limit=3, window=60)
    assert ratelimit.allow("b", limit=3, window=60)


def test_count_and_hit(clock):
    ratelimit.hit("a")
    ratelimit.hit("a")
    assert ratelimit.count("a") == 2
    assert ratelimit.count("other") == 0
    clock[0] += 3601
    assert ratelimit.count("a") == 0


def test_old_keys_are_dropped_when_there_are_many(clock, monkeypatch):
    monkeypatch.setattr(ratelimit, "_MAX_KEYS", 10)
    for i in range(20):
        ratelimit.hit(f"ip{i}")
    clock[0] += 4000
    ratelimit.hit("new")
    assert set(ratelimit._hits) == {"new"}
