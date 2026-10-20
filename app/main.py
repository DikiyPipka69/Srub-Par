"""Демо-сайт магазина бань «Сруб и пар»: FastAPI + SQLite + админка для товаров."""
import os
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import db, ratelimit

load_dotenv()  # читает .env при локальном запуске; на Render переменные задаются в панели

BASE_DIR = Path(__file__).resolve().parent.parent
DOCS_ENABLED = bool(os.environ.get("ENABLE_DOCS"))
DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")
BAD_DATA = "Проверьте введённые данные"
CATEGORIES = {"barrel": "Бани-бочки", "frame": "Каркасные бани", "timber": "Бани из бруса"}

CSP = (
    "default-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    yield


app = FastAPI(
    lifespan=lifespan,
    docs_url="/docs" if DOCS_ENABLED else None,
    redoc_url=None,
    openapi_url="/openapi.json" if DOCS_ENABLED else None,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if request.url.path not in DOCS_PATHS:  # Swagger грузит скрипты с CDN
        response.headers["Content-Security-Policy"] = CSP
    return response


@app.exception_handler(RequestValidationError)
async def on_validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": BAD_DATA})


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


class Lead(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=2, max_length=60)
    phone: str = Field(max_length=30)
    comment: str = Field(default="", max_length=500)
    consent: bool
    website: str = Field(default="", max_length=200)  # ловушка для ботов, у людей пусто
    product_id: int | None = Field(default=None, ge=1)

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
@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/products")
def products():
    return {
        "categories": [{"id": k, "name": v} for k, v in CATEGORIES.items()],
        "products": db.list_products(),
    }


@app.post("/api/lead")
def create_lead(lead: Lead, request: Request):
    if lead.website:  # заполнил бот: делаем вид, что всё хорошо, ничего не сохраняя
        return {"ok": True}
    product_name = ""
    if lead.product_id is not None:
        product = db.get_product(lead.product_id)
        if product is None or not product["in_stock"]:
            raise HTTPException(status_code=422, detail=BAD_DATA)
        product_name = product["name"]  # название берём из базы, а не из запроса
    ip = request.client.host if request.client else "unknown"
    if not ratelimit.allow(ip):
        raise HTTPException(status_code=429, detail="Слишком много заявок, попробуйте позже")
    db.add_lead(lead.name, lead.phone, lead.comment, lead.product_id, product_name)
    return {"ok": True}


# ---------- админка ----------
security = HTTPBasic(auto_error=False)


def admin_auth(creds: HTTPBasicCredentials | None = Depends(security)) -> None:
    password = os.environ.get("ADMIN_PASSWORD")
    if not password:
        raise HTTPException(status_code=404)  # админка выключена, пока не задан пароль
    ok = (
        creds is not None
        and secrets.compare_digest(creds.username.encode(), b"admin")
        and secrets.compare_digest(creds.password.encode(), password.encode())
    )
    if not ok:
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


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
