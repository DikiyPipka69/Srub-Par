"""Тесты слоя базы данных. FastAPI здесь не нужен."""
import sqlite3

import pytest

from app import db


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    db.init()


def test_seed_is_added_once():
    db.init()
    db.init()
    assert db.count_products() == len(db.SEED) == len(db.list_products())


def test_product_crud():
    product = db.add_product("barrel", "Тест'; DROP TABLE products;--", "3 м", 100000, "d", True)
    assert db.get_product(product["id"])["name"].startswith("Тест'")
    updated = db.update_product(product["id"], "frame", "Новое имя", "5 м", 120000, "d2", False)
    assert updated["category"] == "frame" and updated["in_stock"] is False and updated["price"] == 120000
    assert db.update_product(99999, "frame", "x", "", 1000, "", True) is None
    assert db.delete_product(product["id"]) is True
    assert db.delete_product(product["id"]) is False
    assert db.count_products() == len(db.SEED)


def test_leads_keep_product_name_after_product_deleted():
    product = db.add_product("barrel", "Баня", "", 100000, "", True)
    db.add_lead("Иван", "79001234567", "", product["id"], product["name"])
    db.delete_product(product["id"])
    assert db.list_leads()[0]["product_name"] == "Баня"


def test_recent_duplicate_detection():
    assert not db.has_recent_lead("79001234567")
    db.add_lead("Иван", "79001234567", "", None, "")
    assert db.has_recent_lead("79001234567")
    assert not db.has_recent_lead("79007654321")


def test_old_lead_is_not_recent():
    db.add_lead("Иван", "79001234567", "", None, "")
    conn = sqlite3.connect(db._path())
    conn.execute("UPDATE leads SET created_at = datetime('now', '-1 hour')")
    conn.commit()
    conn.close()
    assert not db.has_recent_lead("79001234567")


def test_old_leads_are_trimmed(monkeypatch):
    monkeypatch.setattr(db, "MAX_LEADS", 5)
    for i in range(9):
        db.add_lead(f"Имя{i}", f"7900000000{i}", "", None, "")
    leads = db.list_leads()
    assert len(leads) == 5 and leads[0]["name"] == "Имя8"
