"""Тесты токенов формы. FastAPI здесь не нужен."""
import pytest

from app import formtoken


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setenv("FORM_MIN_AGE", "1.5")
    formtoken.reset()
    now = [1_000_000.0]
    monkeypatch.setattr(formtoken, "_now", lambda: now[0])
    yield now
    formtoken.reset()


def test_valid_token_works_once(fresh):
    token = formtoken.issue()
    fresh[0] += 2
    assert formtoken.verify(token) == "ok"
    assert formtoken.verify(token) == "used"


def test_too_fast_then_ok_later(fresh):
    token = formtoken.issue()
    assert formtoken.verify(token) == "too_fast"
    fresh[0] += 2
    assert formtoken.verify(token) == "ok"  # неудачная попытка токен не сжигает


def test_expired(fresh):
    token = formtoken.issue()
    fresh[0] += formtoken.MAX_AGE + 5
    assert formtoken.verify(token) == "expired"


def test_tampered_signature_and_garbage(fresh):
    token = formtoken.issue()
    fresh[0] += 2
    ts, nonce, sig = token.split(".")
    flipped = ("0" if sig[0] != "0" else "1") + sig[1:]
    assert formtoken.verify(f"{ts}.{nonce}.{flipped}") == "invalid"
    # подмена времени: подпись не совпадёт
    assert formtoken.verify(f"{int(ts) - 10_000_000}.{nonce}.{sig}") == "invalid"
    for garbage in ["", "a.b", "1.2.3", "...", "x" * 500, "١٢٣.abc.def"]:
        assert formtoken.verify(garbage) == "invalid"


def test_tokens_are_unique(fresh):
    assert len({formtoken.issue() for _ in range(50)}) == 50


def test_used_set_stays_bounded(fresh, monkeypatch):
    monkeypatch.setattr(formtoken, "_MAX_USED", 5)
    for _ in range(20):
        token = formtoken.issue()
        fresh[0] += 2
        assert formtoken.verify(token) == "ok"
    assert len(formtoken._used) <= 6
