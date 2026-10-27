"""Работа с SQLite: товары и заявки. Только стандартная библиотека, без ORM."""
import os
import sqlite3
from contextlib import closing
from pathlib import Path

MAX_LEADS = 2000  # хранится не больше стольких последних заявок: диск не переполнить

# Начальные товары. Добавляются, только если таблица пустая.
SEED = [
    ("barrel", "Баня-бочка овальная 3 м", "3 м, до 4 человек", 204000, "Компактная баня-бочка для участка. Печь в комплект не входит."),
    ("barrel", "Баня-бочка квадро 4 м", "4 м, до 6 человек", 243000, "Парная и небольшая комната отдыха. Сборка за один день."),
    ("frame", "Каркасная баня 3×4 м", "3×4 м, парная и душевая", 390000, "Бюджетный вариант для дачи, утепление и отделка внутри."),
    ("frame", "Каркасная баня 4×6 м", "4×6 м, парная, душевая, комната отдыха", 640000, "Полноценная баня с комнатой отдыха и верандой."),
    ("timber", "Баня из бруса 4×4 м", "4×4 м, парная и предбанник", 520000, "Классическая баня из профилированного бруса."),
    ("timber", "Баня из бруса 6×6 м", "6×6 м, парная, комната отдыха, терраса", 980000, "Большая баня для семьи с террасой."),
]


def _path() -> str:
    return os.environ.get("DB_PATH", "data/shop.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_path(), timeout=5)  # при занятой базе ждём, а не падаем
    conn.row_factory = sqlite3.Row
    return conn


def _product(row) -> dict:
    item = dict(row)
    item["in_stock"] = bool(item["in_stock"])
    return item


def init() -> None:
    Path(_path()).parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                name TEXT NOT NULL,
                size TEXT NOT NULL DEFAULT '',
                price INTEGER NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                in_stock INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT NOT NULL,
                comment TEXT NOT NULL DEFAULT '',
                product_id INTEGER,
                product_name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        if conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO products (category, name, size, price, description) VALUES (?, ?, ?, ?, ?)",
                SEED,
            )
        conn.commit()


# ---------- товары ----------
def list_products() -> list[dict]:
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT * FROM products ORDER BY id").fetchall()
    return [_product(r) for r in rows]


def get_product(product_id: int) -> dict | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    return _product(row) if row else None


def add_product(category, name, size, price, description, in_stock) -> dict:
    # Параметры подставляются через ?, поэтому SQL-инъекции невозможны.
    with closing(_connect()) as conn:
        cur = conn.execute(
            "INSERT INTO products (category, name, size, price, description, in_stock) VALUES (?, ?, ?, ?, ?, ?)",
            (category, name, size, price, description, int(in_stock)),
        )
        conn.commit()
        new_id = cur.lastrowid
    return get_product(new_id)


def update_product(product_id, category, name, size, price, description, in_stock) -> dict | None:
    with closing(_connect()) as conn:
        cur = conn.execute(
            "UPDATE products SET category=?, name=?, size=?, price=?, description=?, in_stock=? WHERE id=?",
            (category, name, size, price, description, int(in_stock), product_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
    return get_product(product_id)


def delete_product(product_id: int) -> bool:
    with closing(_connect()) as conn:
        cur = conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
        conn.commit()
        return cur.rowcount > 0


# ---------- заявки ----------
def add_lead(name, phone, comment, product_id, product_name) -> int:
    with closing(_connect()) as conn:
        cur = conn.execute(
            "INSERT INTO leads (name, phone, comment, product_id, product_name) VALUES (?, ?, ?, ?, ?)",
            (name, phone, comment, product_id, product_name),
        )
        new_id = cur.lastrowid
        conn.execute("DELETE FROM leads WHERE id <= ?", (new_id - MAX_LEADS,))
        conn.commit()
        return new_id


def list_leads(limit: int = 200) -> list[dict]:
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT * FROM leads ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def has_recent_lead(phone: str, minutes: int = 10) -> bool:
    """Была ли заявка с этого телефона за последние минуты (двойной клик или спам)."""
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT 1 FROM leads WHERE phone = ? AND created_at >= datetime('now', ?) LIMIT 1",
            (phone, f"-{minutes} minutes"),
        ).fetchone()
    return row is not None


def count_products() -> int:
    with closing(_connect()) as conn:
        return conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
