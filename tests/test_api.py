import pytest
from fastapi.testclient import TestClient

from app import db, formtoken, ratelimit
from app.main import app

PASSWORD = "secret-password-123"
AUTH = ("admin", PASSWORD)
ENDPOINT = "/api/products"  # любой публичный GET-маршрут
FETCH = {"X-Requested-With": "fetch"}
PRODUCT = {"category": "barrel", "name": "Тестовая баня", "size": "3 м", "price": 100000, "description": "Описание", "in_stock": True}
LEAD = {"name": "Анна", "phone": "+7 900 123-45-67", "comment": "", "consent": True, "website": ""}


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Свежая база на каждый тест, известный пароль админки, без ожидания перед отправкой формы
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("ADMIN_PASSWORD", PASSWORD)
    monkeypatch.setenv("FORM_MIN_AGE", "0")
    monkeypatch.delenv("RENDER", raising=False)
    ratelimit.reset()
    formtoken.reset()
    with TestClient(app) as c:
        yield c


def send(client, **patch):
    """Отправляет заявку так, как это делает страница: сначала токен, потом форма."""
    token = client.get("/api/form-token").json()["token"]
    return client.post("/api/lead", json={**LEAD, "token": token, **patch})


# ---------- общее: доступность и защитные заголовки ----------
def test_health_get_and_head(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.head("/api/health").status_code == 200


def test_index_get_and_head(client):
    assert client.get("/").status_code == 200
    assert client.head("/").status_code == 200


def test_security_headers(client):
    res = client.get("/")
    for name in (
        "content-security-policy", "x-frame-options", "x-content-type-options",
        "permissions-policy", "cross-origin-opener-policy", "cross-origin-resource-policy",
    ):
        assert name in res.headers, name
    assert "strict-transport-security" not in res.headers  # по обычному http HSTS не нужен


def test_hsts_over_https(client):
    with TestClient(app, base_url="https://testserver") as secure:
        assert "max-age" in secure.get("/").headers["strict-transport-security"]


def test_api_responses_are_not_cached(client):
    assert client.get(ENDPOINT).headers["cache-control"] == "no-store"


def test_no_cors_headers(client):
    res = client.get(ENDPOINT, headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in res.headers


# ---------- общее: защита от перегрузки ----------
def test_global_rate_limit(client, monkeypatch):
    monkeypatch.setattr("app.main.RATE_LIMIT_PER_MIN", 5)
    codes = [client.get(ENDPOINT).status_code for _ in range(7)]
    assert codes == [200] * 5 + [429] * 2
    blocked = client.get(ENDPOINT)
    assert blocked.headers["retry-after"] == "60"
    assert "content-security-policy" in blocked.headers  # защитные заголовки есть и у отказов
    assert client.get("/api/health").status_code == 200  # проверка здоровья лимитом не ограничивается


def test_oversized_body_is_rejected(client):
    res = client.post("/api/lead", content=b"x" * 20000, headers={"Content-Type": "application/json"})
    assert res.status_code == 413
    assert db.list_leads() == []


# ---------- общее: токен формы ----------
def test_form_token_endpoint(client):
    assert client.get("/api/form-token").json()["token"].count(".") == 2


def test_form_token_endpoint_is_rate_limited(client):
    codes = [client.get("/api/form-token").status_code for _ in range(32)]
    assert codes[:30] == [200] * 30 and codes[30:] == [429, 429]


def test_lead_requires_token(client):
    assert client.post("/api/lead", json=LEAD).status_code == 422


@pytest.mark.parametrize("bad", ["garbage-token-value", "1234567.89abcdef.abc", "x" * 50])
def test_lead_rejects_forged_token(client, bad):
    assert send(client, token=bad).status_code == 400
    assert db.list_leads() == []


def test_token_is_single_use(client):
    token = client.get("/api/form-token").json()["token"]
    assert client.post("/api/lead", json={**LEAD, "token": token}).status_code == 200
    assert client.post("/api/lead", json={**LEAD, "token": token}).status_code == 400


def test_form_submitted_too_fast_is_rejected(client, monkeypatch):
    monkeypatch.setenv("FORM_MIN_AGE", "60")
    assert send(client).status_code == 400
    assert db.list_leads() == []


# ---------- общее: заявки ----------
def test_lead_is_saved_with_clean_phone(client):
    assert send(client).status_code == 200
    lead = db.list_leads()[0]
    assert lead["phone"] == "79001234567" and lead["name"] == "Анна"


@pytest.mark.parametrize("patch", [{"consent": False}, {"phone": "123"}, {"name": "А"}, {"name": "А" * 61}])
def test_invalid_lead_is_rejected(client, patch):
    assert send(client, **patch).status_code == 422
    assert db.list_leads() == []


def test_honeypot_is_not_saved(client):
    assert send(client, website="http://spam.example").status_code == 200
    assert db.list_leads() == []


@pytest.mark.parametrize("name", ["http://spam.ru", "Купи www.spam.ru"])
def test_spam_in_name_is_not_saved(client, name):
    assert send(client, name=name).status_code == 200  # боту отвечаем «ок», но ничего не сохраняем
    assert db.list_leads() == []


def test_invisible_characters_are_removed(client):
    assert send(client, name="Анна\u202e\u200b").status_code == 200
    assert db.list_leads()[0]["name"] == "Анна"


def test_duplicate_lead_is_ignored(client):
    assert send(client).status_code == 200
    assert send(client, name="Анна Другая").status_code == 200  # тот же телефон: считается повтором
    assert len(db.list_leads()) == 1


def test_lead_rate_limit(client):
    for i in range(5):
        assert send(client, phone=f"+7 900 000-00-{i:02d}").status_code == 200
    assert send(client, phone="+7 900 000-00-99").status_code == 429


# ---------- общее: админка ----------
def test_admin_is_disabled_without_password(client, monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD")
    assert client.get("/admin").status_code == 404


def test_weak_password_is_refused_on_production(client, monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("ADMIN_PASSWORD", "short")
    assert client.get("/admin", auth=("admin", "short")).status_code == 404
    monkeypatch.setenv("ADMIN_PASSWORD", "a-long-unique-passphrase-2026")
    assert client.get("/admin", auth=("admin", "a-long-unique-passphrase-2026")).status_code == 200


def test_admin_requires_correct_credentials(client):
    assert client.get("/admin").status_code == 401
    assert client.get("/admin", auth=("admin", "wrong")).status_code == 401
    assert client.get("/admin", auth=("root", PASSWORD)).status_code == 401
    assert client.get("/admin", auth=AUTH).status_code == 200


def test_admin_locks_out_after_failed_attempts(client):
    for _ in range(10):
        assert client.get("/admin", auth=("admin", "wrong")).status_code == 401
    # после десяти неудач вход закрыт даже с правильным паролем
    assert client.get("/admin", auth=AUTH).status_code == 429


# ---------- общее: страницы ошибок ----------
def test_unknown_page_gives_html_404(client):
    res = client.get("/no-such-page")
    assert res.status_code == 404
    assert "text/html" in res.headers["content-type"]


def test_unknown_api_route_gives_json_404(client):
    res = client.get("/api/no-such-route")
    assert res.status_code == 404
    assert res.json() == {"detail": "Not Found"}


# ---------- Сруб и пар: каталог и админка ----------
def test_public_catalog_is_seeded(client):
    data = client.get("/api/products").json()
    assert len(data["categories"]) == 3 and len(data["products"]) >= 6
    assert all(p["name"] and p["price"] > 0 for p in data["products"])


def test_lead_saves_product_name_from_db(client):
    pid = client.get("/api/products").json()["products"][0]["id"]
    assert send(client, product_id=pid, product_name="Подмена").status_code == 200
    lead = db.list_leads()[0]
    assert lead["product_id"] == pid and lead["product_name"] not in ("", "Подмена")


def test_lead_rejects_unknown_or_out_of_stock_product(client):
    assert send(client, product_id=9999).status_code == 422
    hidden = db.add_product("frame", "Нет в наличии", "", 50000, "", False)
    assert send(client, product_id=hidden["id"]).status_code == 422
    assert db.list_leads() == []


def test_admin_page_and_leads(client):
    page = client.get("/admin", auth=AUTH)
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert page.headers["cache-control"] == "no-store"
    send(client)
    leads = client.get("/api/admin/leads", auth=AUTH).json()
    assert len(leads) == 1 and leads[0]["name"] == "Анна"


def test_admin_api_requires_auth(client):
    assert client.get("/api/admin/leads").status_code == 401
    assert client.get("/api/admin/leads", auth=("admin", "wrong")).status_code == 401
    assert client.post("/api/admin/products", json=PRODUCT, headers=FETCH).status_code == 401
    assert client.delete("/api/admin/products/1", headers=FETCH).status_code == 401


def test_admin_product_crud(client):
    created = client.post("/api/admin/products", json=PRODUCT, headers=FETCH, auth=AUTH)
    assert created.status_code == 200
    pid = created.json()["id"]
    assert any(p["id"] == pid for p in client.get("/api/products").json()["products"])

    updated = client.put(
        f"/api/admin/products/{pid}", json={**PRODUCT, "price": 120000, "in_stock": False},
        headers=FETCH, auth=AUTH,
    )
    assert updated.json()["price"] == 120000 and updated.json()["in_stock"] is False

    assert client.delete(f"/api/admin/products/{pid}", headers=FETCH, auth=AUTH).status_code == 200
    assert db.get_product(pid) is None
    assert client.delete(f"/api/admin/products/{pid}", headers=FETCH, auth=AUTH).status_code == 404


def test_admin_writes_need_fetch_header(client):
    """Без заголовка X-Requested-With запись запрещена: защита от CSRF."""
    count = len(db.list_products())
    assert client.post("/api/admin/products", json=PRODUCT, auth=AUTH).status_code == 403
    assert client.put("/api/admin/products/1", json=PRODUCT, auth=AUTH).status_code == 403
    assert client.delete("/api/admin/products/1", auth=AUTH).status_code == 403
    assert len(db.list_products()) == count


@pytest.mark.parametrize(
    "patch", [{"category": "unknown"}, {"price": 10}, {"price": 10**9}, {"name": "Б"}],
)
def test_admin_validates_product(client, patch):
    res = client.post("/api/admin/products", json={**PRODUCT, **patch}, headers=FETCH, auth=AUTH)
    assert res.status_code == 422


def test_admin_product_limit(client, monkeypatch):
    monkeypatch.setattr("app.main.MAX_PRODUCTS", len(db.list_products()))
    assert client.post("/api/admin/products", json=PRODUCT, headers=FETCH, auth=AUTH).status_code == 409
