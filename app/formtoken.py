"""Одноразовые подписанные токены для формы заявки.

Страница при загрузке получает токен (GET /api/form-token), а при отправке
возвращает его. Сервер проверяет подпись, возраст и то, что токен не использовался.
Это барьер от простых ботов, которые шлют запросы в обход страницы.
"""
import hashlib
import hmac
import os
import re
import secrets
import time

MAX_AGE = 3600      # через час токен протухает
_MAX_USED = 10000   # предел запомненных использованных токенов
_used: dict[str, float] = {}
_secret_cache: bytes | None = None
_TS = re.compile(r"[0-9]{1,16}")
_NONCE = re.compile(r"[0-9a-f]{1,32}")


def _now() -> float:
    return time.time()


def _secret() -> bytes:
    """Ключ подписи: SECRET_KEY из окружения, иначе случайный на время жизни процесса."""
    global _secret_cache
    if _secret_cache is None:
        env = os.environ.get("SECRET_KEY", "")
        _secret_cache = env.encode() if env else secrets.token_bytes(32)
    return _secret_cache


def min_age() -> float:
    """Минимальное время (сек) между выдачей токена и отправкой формы."""
    return float(os.environ.get("FORM_MIN_AGE", "1.5"))


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()[:32]


def issue() -> str:
    payload = f"{int(_now() * 1000)}.{secrets.token_hex(8)}"
    return f"{payload}.{_sign(payload)}"


def verify(token: str) -> str:
    """Возвращает "ok" или причину отказа: invalid, too_fast, expired, used.

    Успешно проверенный токен сразу помечается использованным.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return "invalid"
    ts, nonce, signature = parts
    if not (_TS.fullmatch(ts) and _NONCE.fullmatch(nonce)):
        return "invalid"
    if not hmac.compare_digest(signature, _sign(f"{ts}.{nonce}")):
        return "invalid"
    age = _now() - int(ts) / 1000
    if age < min_age():
        return "too_fast"
    if age > MAX_AGE:
        return "expired"
    if token in _used:
        return "used"
    _cleanup()
    _used[token] = _now() + MAX_AGE
    return "ok"


def _cleanup() -> None:
    if len(_used) <= _MAX_USED:
        return
    now = _now()
    for key in [k for k, expires in _used.items() if expires < now]:
        del _used[key]
    if len(_used) > _MAX_USED:  # атака свежими токенами: проще начать с чистого листа
        _used.clear()


def reset() -> None:
    _used.clear()
