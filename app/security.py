"""Защитные функции без привязки к FastAPI: их легко тестировать отдельно."""
import json
import re

MAX_BODY = 16 * 1024  # байт: самое большое тело запроса, которое принимает сайт

CSP = (
    "default-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")

# Пароли, которые нельзя использовать на боевом сервере (в том числе пример из .env.example)
WEAK_PASSWORDS = {"changeme", "password", "admin", "12345678", "qwerty123", "mydemopass2026"}
MIN_PASSWORD_LEN = 12


def response_headers(path: str, https: bool) -> dict[str, str]:
    """Защитные заголовки для ответа."""
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
    }
    if path not in DOCS_PATHS:  # Swagger грузит скрипты с CDN, ему политику не ставим
        headers["Content-Security-Policy"] = CSP
    if https:
        headers["Strict-Transport-Security"] = "max-age=31536000"
    if path.startswith("/api/") or path == "/admin":
        headers["Cache-Control"] = "no-store"  # ответы API и админки не кешируются
    return headers


def admin_password_problem(password: str, production: bool) -> str | None:
    """Почему пароль админки не подходит для боевого сервера (None, если подходит)."""
    if not production:
        return None
    if len(password) < MIN_PASSWORD_LEN:
        return f"короче {MIN_PASSWORD_LEN} символов"
    if password.lower() in WEAK_PASSWORDS:
        return "слишком известный пароль"
    return None


# Управляющие символы, «невидимки» нулевой ширины и символы смены направления текста
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")
_LINK = re.compile(r"(https?://|www\.|\[url|<a\s)", re.IGNORECASE)


def clean_text(value: str) -> str:
    """Убирает невидимые и управляющие символы. Переводы строк и табы остаются."""
    return _CONTROL.sub("", value).strip()


def looks_like_spam(name: str, comment: str = "") -> bool:
    """Грубый признак спама: ссылка в имени или две и более ссылок в комментарии."""
    if _LINK.search(name):
        return True
    return len(_LINK.findall(comment)) >= 2


class BodyLimitMiddleware:
    """Отклоняет запросы с телом больше max_bytes. Чистый ASGI, без зависимостей.

    Проверяет и заявленный Content-Length, и фактически пришедшие байты
    (на случай запроса без длины, кусками): так нельзя забить память огромным телом.
    """

    def __init__(self, app, max_bytes: int = MAX_BODY):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = None
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                declared = value
                break
        if declared is not None:
            try:
                size = int(declared)
            except ValueError:
                size = -1
            if size < 0:
                await self._reply(send, 400, "Некорректный запрос")
                return
            if size > self.max_bytes:
                await self._reply(send, 413, "Слишком большой запрос")
                return

        received = 0
        rejected = False

        async def limited_receive():
            nonlocal received, rejected
            if rejected:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    rejected = True
                    await self._reply(send, 413, "Слишком большой запрос")
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message):
            if not rejected:  # после отказа ответ приложения отбрасываем
                await send(message)

        await self.app(scope, limited_receive, guarded_send)

    @staticmethod
    async def _reply(send, status: int, text: str) -> None:
        body = json.dumps({"detail": text}, ensure_ascii=False).encode()
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})
