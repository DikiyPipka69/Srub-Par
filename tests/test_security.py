"""Тесты защитных функций. FastAPI здесь не нужен."""
import asyncio

import pytest

from app import security


# ---------- заголовки ----------
def test_basic_headers():
    h = security.response_headers("/", https=False)
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in h["Content-Security-Policy"]
    assert "object-src 'none'" in h["Content-Security-Policy"]
    assert h["Cross-Origin-Opener-Policy"] == "same-origin"
    assert h["Cross-Origin-Resource-Policy"] == "same-origin"
    assert "camera=()" in h["Permissions-Policy"]


def test_hsts_only_over_https():
    assert "Strict-Transport-Security" not in security.response_headers("/", https=False)
    assert "max-age" in security.response_headers("/", https=True)["Strict-Transport-Security"]


@pytest.mark.parametrize("path", ["/api/products", "/api/admin/leads", "/admin"])
def test_no_store_for_api_and_admin(path):
    assert security.response_headers(path, https=False)["Cache-Control"] == "no-store"


def test_no_store_not_forced_for_static():
    assert "Cache-Control" not in security.response_headers("/static/css/style.css", https=False)


@pytest.mark.parametrize("path", ["/docs", "/openapi.json"])
def test_docs_have_no_csp(path):
    assert "Content-Security-Policy" not in security.response_headers(path, https=False)


# ---------- пароль админки ----------
def test_password_policy():
    assert security.admin_password_problem("short", production=False) is None
    assert security.admin_password_problem("short", production=True)
    assert security.admin_password_problem("MyDemoPass2026", production=True)  # пример из .env.example
    assert security.admin_password_problem("changeme-changeme", production=True) is None
    assert security.admin_password_problem("x9-Lq-77-kd-Zr2p", production=True) is None


# ---------- очистка текста и спам ----------
def test_clean_text_removes_invisible_and_control_chars():
    assert security.clean_text("  Анна\u200b\u202e\x00  ") == "Анна"
    assert security.clean_text("строка 1\nстрока 2") == "строка 1\nстрока 2"


@pytest.mark.parametrize(
    "name, comment, expected",
    [
        ("Анна", "", False),
        ("Анна", "Хочу баню, вот ссылка на пример https://example.com", False),  # одна ссылка допустима
        ("Анна", "http://a.ru и ещё http://b.ru", True),
        ("http://spam.ru", "", True),
        ("Купи www.spam.ru", "", True),
        ("Анна", "[url=http://x.ru]тут[/url]", True),
    ],
)
def test_looks_like_spam(name, comment, expected):
    assert security.looks_like_spam(name, comment) is expected


# ---------- ограничение размера тела ----------
def _echo_app(calls):
    async def app(scope, receive, send):
        calls.append("called")
        total = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            total += len(message["body"])
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": str(total).encode()})
    return app


def _run(headers, chunks, max_bytes=10000, scope_type="http"):
    calls, sent = [], []
    queue = list(chunks)

    async def receive():
        if queue:
            body = queue.pop(0)
            return {"type": "http.request", "body": body, "more_body": bool(queue)}
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    middleware = security.BodyLimitMiddleware(_echo_app(calls), max_bytes=max_bytes)
    asyncio.run(middleware({"type": scope_type, "method": "POST", "headers": headers}, receive, send))
    statuses = [m["status"] for m in sent if m["type"] == "http.response.start"]
    return statuses, calls


def test_small_body_passes():
    statuses, calls = _run([(b"content-length", b"5")], [b"hello"])
    assert statuses == [200] and calls == ["called"]


def test_declared_size_too_big_is_rejected_before_app():
    statuses, calls = _run([(b"content-length", b"20000")], [b"x"])
    assert statuses == [413] and calls == []


@pytest.mark.parametrize("value", [b"abc", b"-5"])
def test_bad_content_length_is_rejected(value):
    statuses, calls = _run([(b"content-length", value)], [b"x"])
    assert statuses == [400] and calls == []


def test_chunked_body_without_length_is_cut_off():
    # длина не заявлена: считаем байты по ходу. Ответ приложения (200) отбрасывается.
    statuses, calls = _run([], [b"a" * 6000, b"b" * 6000, b"c" * 6000])
    assert statuses == [413]


def test_chunked_small_body_passes():
    statuses, calls = _run([], [b"a" * 100, b"b" * 100])
    assert statuses == [200]


def test_non_http_scope_is_passed_through():
    statuses, calls = _run([], [], scope_type="lifespan")
    assert calls == ["called"]
