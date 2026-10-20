import pytest
from fastapi.testclient import TestClient

from app import db, ratelimit
from app.main import app

AUTH = ("admin", "secret")
FETCH = {"X-Requested-With": "fetch"}
PRODUCT = {
    "category": "barrel",
    "name": "Тестовая баня",
    "size": "3 м",
    "price": 100000,
    "description": "Описание",
    "in_stock": True,
}
LEAD = {"name": "Иван", "phone": "+7 900 123-45-67", "comment": "", "consent": True, "website": ""}


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Свежая база на каждый тест и пароль админки, заданный тестом
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("ADMIN_PASSWORD", "secret")
    ratelimit.reset()
    with TestClient(app) as c:
        yield c


# ---------- публичная часть ----------
def test_health_and_headers(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    res = client.get("/")
    assert res.status_code == 200
    assert "default-src 'self'" in res.headers["content-security-policy"]
    assert res.headers["x-content-type-options"] == "nosniff"


def test_public_catalog_is_seeded(client):
    data = client.get("/api/products").json()
    assert len(data["categories"]) == 3
    assert len(data["products"]) >= 6
    assert all(p["name"] and p["price"] > 0 for p in data["products"])


def test_lead_saves_product_name_from_db(client):
    pid = client.get("/api/products").json()["products"][0]["id"]
    res = client.post("/api/lead", json={**LEAD, "product_id": pid, "product_name": "Подмена"})
    assert res.status_code == 200
    lead = db.list_leads()[0]
    assert lead["product_id"] == pid
    assert lead["product_name"] != "Подмена" and lead["product_name"]
    assert lead["phone"] == "79001234567"


def test_lead_without_product(client):
    assert client.post("/api/lead", json=LEAD).status_code == 200
    assert db.list_leads()[0]["product_name"] == ""


def test_lead_rejects_unknown_or_out_of_stock_product(client):
    assert client.post("/api/lead", json={**LEAD, "product_id": 9999}).status_code == 422
    hidden = db.add_product("frame", "Нет в наличии", "", 50000, "", False)
    assert client.post("/api/lead", json={**LEAD, "product_id": hidden["id"]}).status_code == 422
    assert db.list_leads() == []


@pytest.mark.parametrize("patch", [{"consent": False}, {"phone": "123"}, {"name": "И"}])
def test_rejects_invalid_lead(client, patch):
    assert client.post("/api/lead", json={**LEAD, **patch}).status_code == 422
    assert db.list_leads() == []


def test_honeypot_is_not_saved(client):
    assert client.post("/api/lead", json={**LEAD, "website": "http://spam.example"}).status_code == 200
    assert db.list_leads() == []


def test_rate_limit(client):
    for _ in range(5):
        assert client.post("/api/lead", json=LEAD).status_code == 200
    assert client.post("/api/lead", json=LEAD).status_code == 429


# ---------- админка ----------
def test_admin_disabled_without_password(client, monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD")
    assert client.get("/admin").status_code == 404
    assert client.get("/api/admin/leads").status_code == 404


def test_admin_requires_auth(client):
    assert client.get("/admin").status_code == 401
    assert client.get("/api/admin/leads", auth=("admin", "wrong")).status_code == 401
    assert client.post("/api/admin/products", json=PRODUCT, headers=FETCH).status_code == 401
    assert client.delete("/api/admin/products/1", headers=FETCH).status_code == 401


def test_admin_page_and_leads(client):
    page = client.get("/admin", auth=AUTH)
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert "script-src 'self'" in page.headers["content-security-policy"]
    client.post("/api/lead", json=LEAD)
    leads = client.get("/api/admin/leads", auth=AUTH).json()
    assert len(leads) == 1 and leads[0]["name"] == "Иван"


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
    "patch",
    [{"category": "unknown"}, {"price": 10}, {"price": 10**9}, {"name": "Б"}],
)
def test_admin_validates_product(client, patch):
    res = client.post("/api/admin/products", json={**PRODUCT, **patch}, headers=FETCH, auth=AUTH)
    assert res.status_code == 422
