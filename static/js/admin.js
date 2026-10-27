"use strict";

const $ = (id) => document.getElementById(id);
const rub = (n) => new Intl.NumberFormat("ru-RU").format(n) + " ₽";

let categories = [];
let products = [];
let flashId = null;   // строка, которую подсветить после сохранения
let editingId = null;  // null: создаём новый товар, число: правим существующий

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;  // textContent: данные из базы не выполняются как код
  if (cls) node.className = cls;
  return node;
}

// Все запросы админки идут с заголовком X-Requested-With: сервер без него отклоняет изменения (защита от CSRF)
async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json", "X-Requested-With": "fetch" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let msg = "Ошибка " + res.status;
    try { const d = await res.json(); if (typeof d.detail === "string") msg = d.detail; } catch { /* ответ не JSON */ }
    throw new Error(msg);
  }
  return res.json();
}

const categoryName = (id) => (categories.find((c) => c.id === id) || { name: id }).name;

// ---------- Товары ----------
function renderProducts() {
  const body = $("p-body");
  body.replaceChildren();
  for (const p of products) {
    const tr = el("tr");
    tr.dataset.id = String(p.id);
    const actions = el("td");
    const edit = el("button", "Изменить", "link");
    edit.type = "button";
    edit.addEventListener("click", () => openForm(p));
    const del = el("button", "Удалить", "link");
    del.type = "button";
    del.addEventListener("click", () => removeProduct(p));
    actions.append(edit, del);
    tr.append(el("td", p.name), el("td", categoryName(p.category)), el("td", rub(p.price)), el("td", p.in_stock ? "Да" : "Нет"), actions);
    body.appendChild(tr);
  }
}

async function loadProducts() {
  const data = await api("GET", "/api/products");
  categories = data.categories;
  products = data.products;
  const sel = $("pf-category");
  if (!sel.options.length) {
    for (const c of categories) sel.appendChild(new Option(c.name, c.id));
  }
  renderProducts();
  flashRow();
}

function flashRow() {
  if (flashId === null) return;
  const row = $("p-body").querySelector(`tr[data-id="${flashId}"]`);
  flashId = null;
  if (row) row.classList.add("flash");
}

function openForm(product) {
  editingId = product ? product.id : null;
  $("p-title").textContent = product ? "Редактирование товара" : "Новый товар";
  $("pf-name").value = product ? product.name : "";
  $("pf-category").value = product ? product.category : categories[0].id;
  $("pf-size").value = product ? product.size : "";
  $("pf-price").value = product ? product.price : "";
  $("pf-desc").value = product ? product.description : "";
  $("pf-stock").checked = product ? product.in_stock : true;
  $("pf-error").textContent = "";
  $("p-form").hidden = false;
  $("pf-name").focus();
}

function closeForm() {
  $("p-form").hidden = true;
  editingId = null;
}

async function removeProduct(product) {
  if (!confirm(`Удалить «${product.name}»?`)) return;
  try {
    await api("DELETE", `/api/admin/products/${product.id}`);
    await loadProducts();
  } catch (err) {
    alert(err.message);
  }
}

$("add-btn").addEventListener("click", () => openForm(null));
$("pf-cancel").addEventListener("click", closeForm);

$("p-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {
    name: $("pf-name").value.trim(),
    category: $("pf-category").value,
    size: $("pf-size").value.trim(),
    price: parseInt($("pf-price").value, 10),
    description: $("pf-desc").value.trim(),
    in_stock: $("pf-stock").checked,
  };
  const err = $("pf-error");
  if (payload.name.length < 2) { err.textContent = "Введите название."; return; }
  if (!Number.isInteger(payload.price) || payload.price < 1000) { err.textContent = "Цена должна быть не меньше 1 000 ₽."; return; }
  err.textContent = "";
  $("pf-save").disabled = true;
  try {
    const saved = editingId === null
      ? await api("POST", "/api/admin/products", payload)
      : await api("PUT", `/api/admin/products/${editingId}`, payload);
    flashId = saved.id;
    closeForm();
    await loadProducts();
  } catch (e) {
    err.textContent = e.message;
  } finally {
    $("pf-save").disabled = false;
  }
});

// ---------- Заявки ----------
async function loadLeads() {
  const body = $("l-body");
  body.replaceChildren();
  const leads = await api("GET", "/api/admin/leads");
  if (!leads.length) {
    const tr = el("tr");
    const td = el("td", "Заявок пока нет");
    td.colSpan = 6;
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }
  for (const l of leads) {
    const tr = el("tr");
    tr.append(el("td", String(l.id)), el("td", l.created_at), el("td", l.name), el("td", l.phone), el("td", l.product_name || "не выбрана"), el("td", l.comment));
    body.appendChild(tr);
  }
}

// ---------- Вкладки ----------
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t === tab));
    $("tab-products").hidden = tab.dataset.tab !== "products";
    $("tab-leads").hidden = tab.dataset.tab !== "leads";
    if (tab.dataset.tab === "leads") loadLeads().catch((e) => alert(e.message));
  });
});

loadProducts().catch((e) => alert(e.message));
