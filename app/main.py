"""Демо-сайт магазина бань «Сруб и пар»: FastAPI + SQLite + админка для товаров."""
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import db, formtoken, ratelimit, security

load_dotenv()  # читает .env при локальном запуске; на Render переменные задаются в панели

log = logging.getLogger("app.security")

BASE_DIR = Path(__file__).resolve().parent.parent
DOCS_ENABLED = bool(os.environ.get("ENABLE_DOCS"))
BAD_DATA = "Проверьте введённые данные"
STALE_FORM = "Форма устарела или отправлена слишком быстро. Подождите секунду и нажмите «Отправить» ещё раз."
CATEGORIES = {"barrel": "Бани-бочки", "frame": "Каркасные бани", "timber": "Бани из бруса"}
MAX_PRODUCTS = 200        # больше товаров через админку добавить нельзя
RATE_LIMIT_PER_MIN = int(os.environ.get("RATE_LIMIT_PER_MIN", "240"))  # запросов в минуту с одного IP
ADMIN_FAIL_LIMIT = 10     # столько неудачных входов подряд...
ADMIN_FAIL_WINDOW = 900   # ...за 15 минут закрывают вход с этого адреса


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def production() -> bool:
    return bool(os.environ.get("RENDER"))  # Render сам задаёт эту переменную


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    password = os.environ.get("ADMIN_PASSWORD")
    problem = security.admin_password_problem(password, production()) if password else None
    if problem:
        log.error("ADMIN_PASSWORD не подходит для боевого сервера (%s): админка отключена", problem)
    yield


app = FastAPI(
    lifespan=lifespan,
    docs_url="/docs" if DOCS_ENABLED else None,
    redoc_url=None,
    openapi_url="/openapi.json" if DOCS_ENABLED else None,
)
app.add_middleware(security.BodyLimitMiddleware, max_bytes=security.MAX_BODY)


@app.middleware("http")
async def guard(request: Request, call_next):
    """Общий лимит запросов с одного IP и защитные заголовки на каждый ответ."""
    path = request.url.path
    ip = client_ip(request)
    if path != "/api/health" and not ratelimit.allow(f"req:{ip}", RATE_LIMIT_PER_MIN, 60):
        log.warning("слишком много запросов ip=%s path=%s", ip, path)
        response = JSONResponse(
            {"detail": "Слишком много запросов, подождите минуту"},
            status_code=429,
            headers={"Retry-After": "60"},
        )
    else:
        response = await call_next(request)
    for name, value in security.response_headers(path, request.url.scheme == "https").items():
        response.headers[name] = value
    return response


# Необязательно: пускать только запросы на свои домены (ALLOWED_HOSTS=example.com,www.example.com)
_hosts = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]
if _hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_hosts)


@app.exception_handler(RequestValidationError)
async def on_validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": BAD_DATA})


@app.exception_handler(StarletteHTTPException)
async def on_http_error(request: Request, exc: StarletteHTTPException):
    # Для страниц сайта показываем красивую 404, для API оставляем JSON
    if exc.status_code == 404 and not request.url.path.startswith("/api"):
        return FileResponse(BASE_DIR / "static" / "404.html", status_code=404)
    return await http_exception_handler(request, exc)


class ProductIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    category: str
    name: str = Field(min_length=2, max_length=80)
    size: str = Field(default="", max_length=60)
    price: int = Field(ge=1000, le=5_000_000)
    description: str = Field(default="", max_length=400)
    in_stock: bool = True

    @field_validator("category")
    @classmethod
    def known_category(cls, value: str) -> str:
        if value not in CATEGORIES:
            raise ValueError("неизвестная категория")
        return value

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = re.sub(r"\s+", " ", security.clean_text(value))
        if len(value) < 2:
            raise ValueError("слишком короткое название")
        return value

    @field_validator("size", "description")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return security.clean_text(value)


class Lead(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=2, max_length=60)
    phone: str = Field(max_length=30)
    comment: str = Field(default="", max_length=500)
    consent: bool
    website: str = Field(default="", max_length=200)  # ловушка для ботов, у людей пусто
    token: str = Field(min_length=10, max_length=100)  # одноразовый токен из /api/form-token
    product_id: int | None = Field(default=None, ge=1)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = re.sub(r"\s+", " ", security.clean_text(value))
        if len(value) < 2:
            raise ValueError("слишком короткое имя")
        return value

    @field_validator("comment")
    @classmethod
    def clean_comment(cls, value: str) -> str:
        return security.clean_text(value)

    @field_validator("phone")
    @classmethod
    def clean_phone(cls, value: str) -> str:
        digits = re.sub(r"\D", "", value)
        if not 10 <= len(digits) <= 15:
            raise ValueError("некорректный телефон")
        return digits

    @field_validator("consent")
    @classmethod
    def need_consent(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("нужно согласие")
        return value


# ---------- публичные маршруты ----------
@app.api_route("/api/health", methods=["GET", "HEAD"])
def health():
    return {"status": "ok"}


@app.get("/api/products")
def products():
    return {
        "categories": [{"id": k, "name": v} for k, v in CATEGORIES.items()],
        "products": db.list_products(),
    }


@app.get("/api/form-token")
def form_token(request: Request):
    if not ratelimit.allow(f"tok:{client_ip(request)}", 30, 60):
        raise HTTPException(status_code=429, detail="Слишком много запросов, подождите минуту")
    return {"token": formtoken.issue()}


@app.post("/api/lead")
def create_lead(lead: Lead, request: Request):
    ip = client_ip(request)
    if lead.website:  # заполнил бот: делаем вид, что всё хорошо, ничего не сохраняя
        log.info("ловушка для ботов сработала ip=%s", ip)
        return {"ok": True}
    status = formtoken.verify(lead.token)
    if status != "ok":
        log.info("токен формы отклонён (%s) ip=%s", status, ip)
        raise HTTPException(status_code=400, detail=STALE_FORM)
    product_name = ""
    if lead.product_id is not None:
        product = db.get_product(lead.product_id)
        if product is None or not product["in_stock"]:
            raise HTTPException(status_code=422, detail=BAD_DATA)
        product_name = product["name"]  # название берём из базы, а не из запроса
    if security.looks_like_spam(lead.name, lead.comment):
        log.info("спам в тексте заявки ip=%s", ip)
        return {"ok": True}
    if not ratelimit.allow(f"lead:{ip}"):
        log.warning("лимит заявок ip=%s", ip)
        raise HTTPException(status_code=429, detail="Слишком много заявок, попробуйте позже")
    if db.has_recent_lead(lead.phone):  # повтор: двойной клик или спам
        return {"ok": True}
    db.add_lead(lead.name, lead.phone, lead.comment, lead.product_id, product_name)
    return {"ok": True}


# ---------- админка ----------
basic = HTTPBasic(auto_error=False)


def admin_auth(request: Request, creds: HTTPBasicCredentials | None = Depends(basic)) -> None:
    password = os.environ.get("ADMIN_PASSWORD")
    if not password or security.admin_password_problem(password, production()):
        raise HTTPException(status_code=404)  # админка выключена: пароля нет или он слабый
    ip = client_ip(request)
    fail_key = f"admin-fail:{ip}"
    if ratelimit.count(fail_key, ADMIN_FAIL_WINDOW) >= ADMIN_FAIL_LIMIT:
        raise HTTPException(status_code=429, detail="Слишком много неудачных попыток, попробуйте позже")
    ok = (
        creds is not None
        and secrets.compare_digest(creds.username.encode(), b"admin")
        and secrets.compare_digest(creds.password.encode(), password.encode())
    )
    if not ok:
        if creds is not None:  # первый запрос браузера без пароля неудачей не считаем
            ratelimit.hit(fail_key)
            log.warning("неудачный вход в админку ip=%s", ip)
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Basic"})


def require_fetch_header(request: Request) -> None:
    """Защита от CSRF. Браузер сам подставляет сохранённый пароль Basic-авторизации
    в запросы с чужих сайтов, а вот добавить свой заголовок чужая страница без
    разрешения сервера не может. Админка всегда шлёт этот заголовок."""
    if request.headers.get("x-requested-with") != "fetch":
        raise HTTPException(status_code=403, detail="Forbidden")


admin_only = [Depends(admin_auth)]
admin_write = [Depends(admin_auth), Depends(require_fetch_header)]


@app.get("/admin", include_in_schema=False, dependencies=admin_only)
def admin_page():
    return FileResponse(
        BASE_DIR / "app" / "admin.html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/admin/leads", dependencies=admin_only)
def admin_leads():
    return db.list_leads()


@app.post("/api/admin/products", dependencies=admin_write)
def admin_create_product(data: ProductIn):
    if db.count_products() >= MAX_PRODUCTS:
        raise HTTPException(status_code=409, detail="Достигнут предел числа товаров")
    return db.add_product(**data.model_dump())


@app.put("/api/admin/products/{product_id}", dependencies=admin_write)
def admin_update_product(product_id: int, data: ProductIn):
    product = db.update_product(product_id, **data.model_dump())
    if product is None:
        raise HTTPException(status_code=404, detail="Товар не найден")
    return product


@app.delete("/api/admin/products/{product_id}", dependencies=admin_write)
def admin_delete_product(product_id: int):
    if not db.delete_product(product_id):
        raise HTTPException(status_code=404, detail="Товар не найден")
    return {"ok": True}


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
