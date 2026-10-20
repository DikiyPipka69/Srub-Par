"use strict";

const $ = (id) => document.getElementById(id);
const rub = (n) => new Intl.NumberFormat("ru-RU").format(n) + " ₽";

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;  // textContent, а не innerHTML: так безопаснее
  if (cls) node.className = cls;
  return node;
}

// ---------- Каталог из API ----------
let catalog = { categories: [], products: [] };
let activeFilter = "all";
const grid = $("grid");
const select = $("f-product");

function renderFilters() {
  const box = $("filters");
  box.replaceChildren();
  const items = [{ id: "all", name: "Все" }, ...catalog.categories];
  for (const item of items) {
    const btn = el("button", item.name);
    btn.type = "button";
    btn.setAttribute("aria-pressed", String(item.id === activeFilter));
    btn.addEventListener("click", () => { activeFilter = item.id; renderFilters(); renderGrid(); });
    box.appendChild(btn);
  }
}

function renderGrid() {
  grid.replaceChildren();
  const list = catalog.products.filter((p) => activeFilter === "all" || p.category === activeFilter);
  if (!list.length) {
    grid.appendChild(el("p", "В этой категории пока нет товаров.", "sub"));
    return;
  }
  for (const p of list) {
    const card = el("article", undefined, "card");
    const ph = el("div", undefined, "ph ph-" + p.category);
    ph.setAttribute("role", "img");
    ph.setAttribute("aria-label", "Заглушка вместо фото");
    const buy = el("div", undefined, "buy");
    buy.appendChild(el("span", rub(p.price), "price"));
    if (p.in_stock) {
      const btn = el("button", "Заказать", "btn sm");
      btn.type = "button";
      btn.addEventListener("click", () => chooseProduct(p.id));
      buy.appendChild(btn);
    } else {
      buy.appendChild(el("span", "Нет в наличии", "out"));
    }
    card.append(ph, el("h3", p.name), el("p", p.size, "meta"), el("p", p.description, "desc"), buy);
    grid.appendChild(card);
  }
}

function fillSelect() {
  select.replaceChildren(new Option("Пока не выбрал, нужна консультация", ""));
  for (const p of catalog.products.filter((x) => x.in_stock)) {
    select.appendChild(new Option(`${p.name} (${rub(p.price)})`, String(p.id)));
  }
}

function chooseProduct(id) {
  select.value = String(id);
  location.hash = "#request";
  $("f-name").focus({ preventScroll: true });
}

async function loadCatalog() {
  try {
    const res = await fetch("/api/products");
    if (!res.ok) throw new Error("bad status");
    catalog = await res.json();
    renderFilters();
    renderGrid();
    fillSelect();
  } catch {
    grid.replaceChildren(el("p", "Не удалось загрузить каталог. Обновите страницу.", "sub"));
  }
}
loadCatalog();

// ---------- Форма заявки -> POST /api/lead ----------
const form = $("lead-form");
const errorBox = $("form-error");
const submitBtn = $("submit-btn");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = $("f-name").value.trim();
  const phone = $("f-phone").value;
  const comment = $("f-comment").value.trim();
  const consent = $("f-consent").checked;
  const website = $("f-website").value;
  const product_id = select.value ? parseInt(select.value, 10) : null;

  if (name.length < 2) { errorBox.textContent = "Введите имя."; return; }
  if (phone.replace(/\D/g, "").length < 10) { errorBox.textContent = "Введите телефон полностью, не меньше 10 цифр."; return; }
  if (!consent) { errorBox.textContent = "Поставьте галочку согласия, иначе заявку отправить нельзя."; return; }

  errorBox.textContent = "";
  submitBtn.disabled = true;
  submitBtn.textContent = "Отправляем…";
  try {
    const res = await fetch("/api/lead", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, phone, comment, consent, website, product_id }),
    });
    if (res.status === 429) {
      errorBox.textContent = "Слишком много заявок. Попробуйте позже.";
    } else if (!res.ok) {
      errorBox.textContent = "Проверьте введённые данные. Возможно, товар уже недоступен: обновите страницу.";
    } else {
      form.hidden = true;
      $("form-ok").hidden = false;
    }
  } catch {
    errorBox.textContent = "Нет связи с сервером. Попробуйте ещё раз.";
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Отправить заявку";
  }
});
