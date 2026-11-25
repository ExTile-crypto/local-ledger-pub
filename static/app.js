/* Local Ledger - single-page app, no build step. */
"use strict";

// ---------------------------------------------------------------- helpers
const $ = (s, el = document) => el.querySelector(s);
const view = () => $("#view");

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (k === "checked" || k === "selected" || k === "disabled") el[k] = !!v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

async function api(path, { method = "GET", body, form } = {}) {
  const opts = { method, headers: {} };
  if (form) opts.body = form;
  else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, opts);
  if (r.status === 401 && path !== "/api/login") { location.href = "/login"; throw new Error("Sign in first."); }
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch {}
    throw new Error(msg);
  }
  return r.json();
}

function toast(msg, err = false) {
  const d = h("div", { class: err ? "err" : "" }, msg);
  $("#toast").append(d);
  setTimeout(() => d.remove(), err ? 9000 : 3500);
}

const store = {
  get(k, d) { try { const v = localStorage.getItem("ledger." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("ledger." + k, JSON.stringify(v)); } catch {} },
};

const S = { settings: {}, categories: [], accounts: [], chat: store.get("chat", []), status: null };
const cur = () => S.settings.base_currency || "EUR";

// privacy mode: every amount is shown as **** (sign kept), toggled from the header
let hideAmounts = store.get("hideAmounts", false);
const MASK = "****";

function money(v, c) {
  if (v == null || v === "") return "";
  const n = Number(v);
  if (hideAmounts) return (n < 0 ? "-" : "") + MASK + (c ? ` ${c}` : "");
  const s = n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return c ? `${s} ${c}` : s;
}
function compact(v) {
  if (hideAmounts) return MASK;
  const a = Math.abs(v);
  if (a >= 1e6) return (v / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
  if (a >= 1e3) return (v / 1e3).toFixed(1).replace(/\.0$/, "") + "k";
  return String(Math.round(v));
}
const amt = (v, c) => h("span", { class: "num" + (v > 0 ? " pos" : "") }, (v > 0 ? "+" : "") + money(v, c));
// small second line under an amount: base-currency value and the original foreign amount
function fxLine(currency, base, origAmount, origCurrency, tx) {
  const parts = [];
  if (currency && currency !== cur()) parts.push(base == null ? "no exchange rate" : "≈ " + money(base, cur()));
  if (origCurrency && origAmount != null && origCurrency !== currency) parts.push(`${origCurrency} ${money(origAmount)}`);
  if (!parts.length && !(tx && tx.fx_source === "missing")) return null;
  const el = h("div", { class: "small muted num" }, parts.join(" · "));
  if (tx && currency !== cur()) {
    el.append(" ", h("button", { class: "btn sm ghost", title: "Set your own exchange rate", onclick: async () => {
      const v = prompt(`Exchange rate ${cur()} per 1 ${currency} for ${tx.date} (empty = use ECB rate):`, tx.fx_source === "manual" ? tx.fx_rate : "");
      if (v === null) return;
      await api(`/api/transactions/${tx.id}`, { method: "PATCH", body: { fx_rate: v.trim() ? Number(v.replace(",", ".")) : 0 } });
      toast("Rate saved"); go("transactions");
    } }, "rate"));
  }
  return el;
}
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));
const today = () => new Date().toISOString().slice(0, 10);

function periods() {
  const d = new Date(), y = d.getFullYear(), m = d.getMonth();
  const iso = (dt) => new Date(dt.getTime() - dt.getTimezoneOffset() * 6e4).toISOString().slice(0, 10);
  return {
    "This month": [iso(new Date(y, m, 1)), iso(new Date(y, m + 1, 0))],
    "Last month": [iso(new Date(y, m - 1, 1)), iso(new Date(y, m, 0))],
    "This quarter": [iso(new Date(y, m - (m % 3), 1)), iso(new Date(y, m - (m % 3) + 3, 0))],
    "This year": [`${y}-01-01`, `${y}-12-31`],
    "Last year": [`${y - 1}-01-01`, `${y - 1}-12-31`],
    "All time": ["", ""],
  };
}

function periodPicker(state, onChange) {
  const from = h("input", { type: "date", value: state.start || "" });
  const to = h("input", { type: "date", value: state.end || "" });
  const chips = h("div", { class: "chips" });
  const P = periods();
  for (const [name, [a, b]] of Object.entries(P)) {
    chips.append(h("button", { class: "chip" + (state.label === name ? " on" : ""), onclick: () => {
      Object.assign(state, { start: a, end: b, label: name }); onChange();
    } }, name));
  }
  const apply = () => { Object.assign(state, { start: from.value, end: to.value, label: "" }); onChange(); };
  from.onchange = apply; to.onchange = apply;
  return h("div", { class: "row" }, chips, h("span", { class: "spacer" }), from, h("span", { class: "muted" }, "–"), to);
}

function catSelect(value, onchange, opts = {}) {
  const sel = h("select", { onchange: (e) => onchange(e.target.value), "aria-label": "Category" });
  if (opts.any) sel.append(h("option", { value: "" }, "All categories"));
  const groups = { income: "Income", expense: "Expenses", transfer: "Transfers", other: "Other" };
  for (const [k, label] of Object.entries(groups)) {
    const og = h("optgroup", { label });
    S.categories.filter((c) => c.kind === k).forEach((c) => og.append(h("option", { value: c.name, selected: c.name === value }, c.name)));
    sel.append(og);
  }
  if (!opts.any && value && !S.categories.some((c) => c.name === value)) sel.prepend(h("option", { value, selected: true }, value));
  return sel;
}

function dropzone(label, hint, accept, multiple, onFiles) {
  const input = h("input", { type: "file", accept, multiple });
  const z = h("label", { class: "drop" }, h("strong", {}, label), h("span", { class: "muted small" }, hint), input);
  input.onchange = () => { if (input.files.length) onFiles([...input.files]); input.value = ""; };
  z.addEventListener("dragover", (e) => { e.preventDefault(); z.classList.add("over"); });
  z.addEventListener("dragleave", () => z.classList.remove("over"));
  z.addEventListener("drop", (e) => { e.preventDefault(); z.classList.remove("over"); if (e.dataTransfer.files.length) onFiles([...e.dataTransfer.files]); });
  return z;
}

const working = (text) => h("div", { class: "working" }, h("span", { class: "spin" }), text);
const notices = (list, kind = "warn") => list && list.length ? h("div", { class: "notice " + kind }, h("ul", {}, list.map((w) => h("li", {}, w)))) : null;
// A table that becomes a list of cards on phones (CSS .rsp): every cell carries its column name as data-label.
// Cell classes: t-main = the card's title, t-amt = the amount beside it, t-hide = not shown on phones.
function table(head, rows, cls = "") {
  const labels = head.map((x) => (typeof x === "string" ? x : x.textContent || ""));
  for (const tr of [rows].flat(Infinity)) {
    if (!(tr instanceof Element)) continue;
    let i = 0;
    for (const td of tr.children) { if (labels[i] && !td.dataset.label) td.dataset.label = labels[i]; i += td.colSpan || 1; }
  }
  return h("div", { class: "tablewrap rsp " + cls }, h("table", {}, h("thead", {}, h("tr", {}, head.map((x) => typeof x === "string" ? h("th", {}, x) : x))), h("tbody", {}, rows)));
}
const narrow = () => matchMedia("(max-width: 900px)").matches;
// stored error texts from the model server ("LLMError: Model server returned HTTP 400: {json}") -> the human part
function friendly(reason) {
  const r = String(reason || "");
  if (/does not support vision/i.test(r)) return "The model that was loaded cannot read images. Load or choose a vision model, then press Retry.";
  const m = r.match(/"message"\s*:\s*"([^"]+)"/);
  return m ? m[1] : r.replace(/^(LLM\w*|ValueError|RuntimeError|Exception):\s*/, "");
}
const fileUrl = (p) => "/files/uploads/" + p.split("/").map(encodeURIComponent).join("/");
const isImg = (p) => /\.(png|jpe?g|webp|bmp|gif|tiff?)$/i.test(p || "");

async function refreshMeta() {
  [S.settings, S.categories, S.accounts] = await Promise.all([api("/api/settings"), api("/api/categories"), api("/api/accounts")]);
}

async function refreshStatus() {
  const pill = $("#llm-pill");
  try {
    S.status = await api("/api/status");
    const l = S.status.llm;
    const o = S.status.ocr || {};
    const needsKey = !l.ok && l.error && l.error.includes("API key");
    pill.className = "pill " + (l.ok ? "ok" : needsKey ? "bad" : "idle");
    pill.textContent = l.ok ? "Model: " + l.using.split("/").pop() + (o.mode === "off" ? "" : o.ok ? " · OCR on" : " · OCR offline") : (l.error && l.error.includes("No model") ? "No model loaded" : needsKey ? "Studio API key needed" : "Model offline");
    pill.title = l.ok ? "Connected to " + S.settings.llm_base_url + (o.mode === "off" ? "" : o.ok ? "\nOCR: " + o.model : "\n" + (o.error || "OCR offline"))
      : (l.error || "") + "\nReceipts and emails wait in the queue and are read when the model is back.";
  } catch (e) { pill.className = "pill bad"; pill.textContent = "App error"; }
}

// ---------------------------------------------------------------- charts (inline SVG)
function showTip(e, html) {
  const t = $("#tip"); t.innerHTML = html; t.hidden = false;
  const x = Math.min(e.clientX + 14, window.innerWidth - t.offsetWidth - 8);
  const y = Math.min(e.clientY + 14, window.innerHeight - t.offsetHeight - 8);
  t.style.left = x + "px"; t.style.top = y + "px";
}
const hideTip = () => { $("#tip").hidden = true; };
const NS = "http://www.w3.org/2000/svg";
function s(tag, attrs = {}, ...kids) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) el.setAttribute(k, v);
  kids.flat().forEach((k) => k != null && el.append(k instanceof Node ? k : document.createTextNode(k)));
  return el;
}
// bar with rounded data-end only, anchored to the baseline
function barPath(x, y0, w, y1) {
  const r = Math.min(4, w / 2, Math.abs(y0 - y1));
  if (y1 < y0) return `M${x},${y0}V${y1 + r}Q${x},${y1} ${x + r},${y1}H${x + w - r}Q${x + w},${y1} ${x + w},${y1 + r}V${y0}Z`;
  return `M${x},${y0}V${y1 - r}Q${x},${y1} ${x + r},${y1}H${x + w - r}Q${x + w},${y1} ${x + w},${y1 - r}V${y0}Z`;
}

function monthlyChart(months, currency) {
  const W = 640, H = 240, L = 46, R = 8, T = 10, B = 26;
  const max = Math.max(1, ...months.flatMap((m) => [m.income, m.expenses]));
  const step = Math.pow(10, Math.floor(Math.log10(max)));
  const top = Math.ceil(max / step) * step;
  const y = (v) => T + (H - T - B) * (1 - v / top);
  const gw = (W - L - R) / Math.max(months.length, 1);
  const bw = Math.min(28, (gw - 10) / 2);
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img", "aria-label": "Income and expenses per month" });
  const grid = s("g", { class: "grid" });
  for (let i = 0; i <= 4; i++) {
    const v = (top / 4) * i;
    grid.append(s("line", { x1: L, x2: W - R, y1: y(v), y2: y(v) }), s("text", { x: L - 6, y: y(v) + 4, "text-anchor": "end" }, compact(v)));
  }
  svg.append(grid);
  months.forEach((m, i) => {
    const gx = L + gw * i + gw / 2;
    svg.append(s("path", { d: barPath(gx - bw - 1, y(0), bw, y(m.income)), fill: "var(--series-1)" }));
    svg.append(s("path", { d: barPath(gx + 1, y(0), bw, y(m.expenses)), fill: "var(--series-2)" }));
    if (months.length <= 14 || i % Math.ceil(months.length / 12) === 0)
      svg.append(s("text", { x: gx, y: H - 8, "text-anchor": "middle" }, months.length > 6 ? m.month.slice(2) : m.month));
    const hit = s("rect", { x: L + gw * i, y: T, width: gw, height: H - T - B, fill: "transparent" });
    hit.addEventListener("mousemove", (e) => showTip(e, `<b>${m.month}</b><div class="r"><span>Income</span><span>${money(m.income)}</span></div><div class="r"><span>Expenses</span><span>${money(m.expenses)}</span></div><div class="r"><span>Net</span><span>${money(m.income - m.expenses)}</span></div><div class="muted small">${currency}</div>`));
    hit.addEventListener("mouseleave", hideTip);
    svg.append(hit);
  });
  svg.append(s("line", { x1: L, x2: W - R, y1: y(0), y2: y(0), class: "axis" }));
  const legend = h("div", { class: "legend" }, h("span", {}, h("i", { style: "background:var(--series-1)" }), "Income"), h("span", {}, h("i", { style: "background:var(--series-2)" }), "Expenses"));
  const tv = h("details", { class: "tv" }, h("summary", {}, "Show as table"),
    table(["Month", "Income", "Expenses", "Net"], months.map((m) => h("tr", {}, h("td", {}, m.month), h("td", { class: "num" }, money(m.income)), h("td", { class: "num" }, money(m.expenses)), h("td", { class: "num" }, money(m.income - m.expenses))))));
  return h("div", {}, legend, svg, tv);
}

function categoryChart(rows, currency) {
  const data = rows.slice(0, 10);
  const W = 640, rowH = 26, L = 190, R = 90;
  const H = data.length * rowH + 6;
  const max = Math.max(1, ...data.map((d) => d.v));
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img", "aria-label": "Expenses by category" });
  data.forEach((d, i) => {
    const yy = i * rowH + 3, w = Math.max(2, ((W - L - R) * d.v) / max);
    svg.append(s("text", { x: L - 8, y: yy + 15, "text-anchor": "end" }, d.name.length > 28 ? d.name.slice(0, 27) + "…" : d.name));
    const r = Math.min(4, w / 2);
    svg.append(s("path", { d: `M${L},${yy + 3}H${L + w - r}Q${L + w},${yy + 3} ${L + w},${yy + 3 + r}V${yy + 19 - r}Q${L + w},${yy + 19} ${L + w - r},${yy + 19}H${L}Z`, fill: "var(--series-1)" }));
    svg.append(s("text", { x: L + w + 6, y: yy + 15 }, compact(d.v)));
    const hit = s("rect", { x: 0, y: yy, width: W, height: rowH, fill: "transparent" });
    hit.addEventListener("mousemove", (e) => showTip(e, `<b>${esc(d.name)}</b><div class="r"><span>Total</span><span>${money(d.v, currency)}</span></div><div class="r"><span>Transactions</span><span>${d.n}</span></div>`));
    hit.addEventListener("mouseleave", hideTip);
    svg.append(hit);
  });
  const tv = h("details", { class: "tv" }, h("summary", {}, "Show as table"),
    table(["Category", "Total", "Transactions"], rows.map((d) => h("tr", {}, h("td", {}, d.name), h("td", { class: "num" }, money(d.v)), h("td", { class: "num" }, d.n)))));
  return h("div", {}, svg, tv);
}

// ---------------------------------------------------------------- tabs
const ICON = {
  overview: "M4 19V5m0 14h16M8 15l3-4 3 2 4-6", assistant: "M5 5h14v10H10l-5 4z",
  transactions: "M9 7h10M9 12h10M9 17h10M5 7h.01M5 12h.01M5 17h.01", statements: "M12 4v10m0 0l-4-4m4 4l4-4M5 17v3h14v-3",
  cash: "M3 7h18v10H3zM9.5 12a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0", receipts: "M6 3h12v18l-3-2-3 2-3-2-3 2zM9 8h6M9 12h6",
  invoices: "M7 3h7l4 4v14H7zM14 3v4h4M10 12h5M10 16h5", reconcile: "M8.5 12l2.5 2.5 4.5-5M4 12a8 8 0 1 0 16 0a8 8 0 1 0-16 0",
  reports: "M4 5h16v14H4zM4 10h16M10 10v9", settings: "M4 7h10M18 7h2M4 17h4M12 17h8M16 5v4M10 15v4", more: "M5 12h.01M12 12h.01M19 12h.01",
};
const icon = (name) => s("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" }, s("path", { d: ICON[name] }));
// sidebar groups; on phones the first four of BOTTOM plus "More" form the bottom bar
const NAV = [
  [null, [["overview", "Overview"], ["assistant", "Assistant"]]],
  ["Money", [["transactions", "Transactions"], ["statements", "Bank statements"], ["cash", "Cash"]]],
  ["Documents", [["receipts", "Receipts"], ["invoices", "Invoices"], ["reconcile", "Reconcile"]]],
  ["Output", [["reports", "Reports"]]],
  ["App", [["settings", "Settings"]]],
];
const TABS = NAV.flatMap(([, items]) => items);
const BOTTOM = ["overview", "receipts", "transactions", "assistant"];
const closeSheet = () => { $("#sheet").classList.remove("open"); $("#scrim").classList.remove("open"); };
const openSheet = () => { $("#sheet").classList.add("open"); $("#scrim").classList.add("open"); };

function drawNav() {
  const btn = (id, label, cls) => h("button", { role: "tab", "data-tab": id, class: cls, onclick: () => { closeSheet(); go(id); } }, icon(id), h("span", {}, label));
  const nav = $("#tabs"), bottom = $("#bottom"), sheet = $("#sheet");
  nav.innerHTML = bottom.innerHTML = sheet.innerHTML = "";
  for (const [group, items] of NAV) {
    if (group) { nav.append(h("div", { class: "group" }, group)); sheet.append(h("div", { class: "group" }, group)); }
    for (const [id, label] of items) { nav.append(btn(id, label)); sheet.append(btn(id, label, "item")); }
  }
  for (const id of BOTTOM) bottom.append(btn(id, TABS.find(([t]) => t === id)[1]));
  bottom.append(h("button", { id: "more-btn", "aria-label": "All sections", onclick: openSheet }, icon("more"), h("span", {}, "More")));
  $("#scrim").onclick = closeSheet;
}

// small counters in the navigation: what is waiting for you
async function updateBadges() {
  let t;
  try { t = await api("/api/todo"); } catch { return; }
  S.todo = t;
  const counts = { transactions: [t.uncategorized, ""], receipts: [t.receipts_to_review, t.upload_errors + t.email_errors ? "warn" : ""], invoices: [t.invoices_overdue, "warn"], reconcile: [t.unknown_cards, ""] };
  for (const b of document.querySelectorAll("[data-tab]")) {
    b.querySelector(".count")?.remove();
    const [n, cls] = counts[b.dataset.tab] || [0, ""];
    if (n) b.append(h("span", { class: "count " + cls, title: "waiting for you" }, n > 999 ? "999+" : n));
  }
}

const R = {};

const ovState = store.get("ovPeriod", { label: "This year", ...{ start: periods()["This year"][0], end: periods()["This year"][1] } });

R.overview = async (root) => {
  const through = h("span", { class: "badge", style: "display:none" });
  root.append(h("div", { class: "head" }, h("div", {}, h("h1", {}, S.settings.company_name || "Overview"), h("p", { class: "sub" }, "What came in, what went out, and what is waiting for you.")), through));
  const body = h("div", {}, working("Loading…"));
  root.append(h("div", { class: "card" }, periodPicker(ovState, () => { store.set("ovPeriod", ovState); go("overview"); })), body);
  const d = await api(`/api/overview?start=${ovState.start || ""}&end=${ovState.end || ""}`);
  body.innerHTML = "";
  const t = d.totals, c = d.currency, biz = d.books_type === "business";
  const banks = d.accounts.filter((a) => a.kind !== "cash" && a.last_date);
  const newest = banks.map((a) => a.last_date).sort().pop();
  if (newest) { through.textContent = "Statements through " + newest; through.style.display = ""; through.title = "The newest bank transaction in the ledger"; }
  if (!d.accounts.some((a) => a.last_date)) {
    body.append(h("div", { class: "card" }, h("h2", {}, "Get started"),
      h("ol", {}, h("li", {}, "Settings → Your books: say whose books these are and your base currency."),
        h("li", {}, "Bank statements: upload a CSV, Excel or PDF export from your bank."),
        h("li", {}, "Receipts: drop photos or PDFs, or connect an email inbox - they are read by your model and matched to bank lines."),
        h("li", {}, "Ask the Assistant, or create an Excel overview under Reports.")),
      h("div", { class: "row" }, h("button", { class: "btn primary", onclick: () => go("statements") }, "Import a bank statement"),
        h("button", { class: "btn", onclick: () => go("receipts") }, "Add receipts"))));
    return;
  }
  body.append(todoCard(d, banks));

  // the money
  const open = Object.fromEntries((d.open_invoices || []).map((x) => [x.direction, x]));
  const share = t.income > 0 && !hideAmounts ? `${Math.round((t.net / t.income) * 100)}% of income` : c;
  const tr = d.transfers || { money_in: 0, money_out: 0, n: 0 };
  body.append(h("div", { class: "kpis" },
    kpi("Income", money(t.income), c),
    kpi(biz ? "Expenses" : "Spending", money(t.expenses), c),
    kpi(biz ? "Net result" : "Left over", money(t.net), t.uncategorized ? `${t.uncategorized} uncategorized lines not counted` : share,
      t.uncategorized ? () => { Object.assign(txState, { only: "uncategorized", start: ovState.start || "", end: ovState.end || "" }); go("transactions"); } : null),
    kpi("Moved out, not spent", money(tr.money_out), `transfers · ${money(tr.money_in)} moved in`),
    biz ? kpi("Open receivables", money(open.sales?.total || 0), `${open.sales?.n || 0} unpaid sales invoices`, () => go("invoices")) : null));
  if (!t.transactions) {
    body.append(notices([`No transactions in this period. ${banks.map((a) => `${a.name} has transactions up to ${a.last_date}`).join("; ")}.`], "info"));
  } else {
    const catRows = d.by_category.filter((x) => x.kind === "expense").map((x) => ({ name: x.category, v: -x.total, n: x.n })).filter((x) => x.v > 0).sort((a, b) => b.v - a.v);
    body.append(h("div", { class: "grid g2" },
      h("div", { class: "card" }, h("h2", {}, `Income and ${biz ? "expenses" : "spending"} per month (${c})`), d.by_month.length ? monthlyChart(d.by_month, c) : h("p", { class: "empty" }, "No data")),
      h("div", { class: "card" }, h("h2", {}, `${biz ? "Expenses" : "Spending"} by category (${c})`), catRows.length ? categoryChart(catRows, c) : h("p", { class: "empty" }, "No expenses"))));
  }
  const extra = [];
  if (d.converted_currencies.length) extra.push(`Converted to ${c} at ECB rates: ${d.converted_currencies.map((o) => `${o.n} transaction(s) in ${o.currency} (${money(o.net, o.currency)} = ${money(o.net_base, c)})`).join(", ")}.`);
  if (d.transactions_without_exchange_rate) extra.push(`${d.transactions_without_exchange_rate} transaction(s) have no exchange rate and are left out of the totals - set a rate in Transactions.`);
  if (extra.length) body.append(notices(extra, "info"));

  // accounts
  body.append(h("div", { class: "card" }, h("h2", {}, "Accounts"), h("div", { class: "accts" }, d.accounts.map((a) => h("div", { class: "acct" },
    h("div", { class: "name" }, a.name, a.kind === "cash" ? h("span", { class: "badge" }, "cash book") : null),
    h("div", { class: "bal" }, a.balance == null ? "–" : money(a.balance, a.currency)),
    h("div", { class: "when" }, a.last_date ? (a.kind === "cash" ? "last entry " : "balance after ") + a.last_date : "no transactions yet"))))));
};

// "Needs attention": everything that waits for the owner (or for the model), most urgent first
function todoCard(d, banks) {
  const td = d.todo || {}, items = [];
  const add = (n, text, sub, btn, fn, cls = "") => { if (n) items.push({ n, text, sub, btn, fn, cls }); };
  add((td.upload_errors || 0) + (td.email_errors || 0), "document(s) could not be read", "Retry them or remove them from the queue.", "Open", () => go("receipts"), "bad");
  add(td.invoices_overdue, "invoice(s) overdue", "Unpaid and past the due date.", "Open", () => go("invoices"), "bad");
  add((td.uploads_waiting || 0) + (td.emails_waiting || 0), "document(s) waiting for the model", "Uploads and email receipts are read automatically when the model on your desktop is online.", "Open", () => go("receipts"));
  add(td.receipts_to_review, "receipt(s) to review", "Check what the model read, then confirm.", "Review", () => go("receipts"), "warn");
  add(td.uncategorized, "uncategorized transaction(s)", "Left out of income and spending until they have a category.", "Categorize", () => { Object.assign(txState, { only: "uncategorized", start: "", end: "", q: "", category: "" }); go("transactions"); }, "warn");
  add(td.unknown_cards, "card(s) on receipts not linked to an account", "Link them so receipts are matched in the right account.", "Link", () => go("reconcile"));
  if (d.books_type === "business") add(d.expenses_without_documents, "expense(s) without a receipt or invoice in this period", "A business needs a bilag for every expense.", "Open", () => go("reconcile"), "warn");
  const now = Date.now();
  for (const a of banks) {
    const days = Math.floor((now - new Date(a.last_date)) / 864e5);
    if (days > 35) add(days, `days since the last transaction in ${a.name}`, "Import a newer statement to bring the books up to date.", "Import", () => go("statements"));
  }
  return h("div", { class: "card" }, h("h2", {}, items.length ? "Needs attention" : "Nothing needs attention"),
    items.length ? h("ul", { class: "todo" }, items.map((it) => h("li", {}, h("span", { class: "n " + it.cls }, it.n),
      h("span", { class: "t" }, it.text, it.sub ? h("small", {}, it.sub) : null), h("button", { class: "btn sm", onclick: it.fn }, it.btn))))
      : h("p", { class: "muted small", style: "margin:0" }, "Everything is read, categorized and reviewed."));
}

function kpi(label, value, hint, onclick) {
  return h("div", { class: "kpi" + (onclick ? " link" : ""), onclick }, h("div", { class: "label" }, label), h("div", { class: "value" }, value), h("div", { class: "hint" }, hint));
}

// ---------- bank statements
R.statements = async (root) => {
  root.append(h("h1", {}, "Bank statements"), h("p", { class: "sub" }, "Upload an export from your bank. CSV/Excel layouts are detected by the model and every row is parsed exactly; PDFs and scans are read by the model and checked against the balances."));
  const acctSel = h("select", {}, h("option", { value: "" }, "Detect from statement / new account"), S.accounts.map((a) => h("option", { value: a.id }, a.name)));
  const area = h("div");
  root.append(h("div", { class: "card" },
    h("div", { class: "row", style: "margin-bottom:10px" }, h("label", { class: "f" }, "Account", acctSel)),
    dropzone("Drop a bank statement here", "CSV, Excel (.xlsx), PDF, scanned image, MT940/CAMT text - or click to choose", ".csv,.tsv,.txt,.xlsx,.xlsm,.pdf,.png,.jpg,.jpeg,.webp,.xml,.sta,.mt940,.ofx,.qif", false,
      async ([f]) => {
        area.innerHTML = ""; area.append(working(`Reading ${f.name} with the model… PDFs can take a minute or two.`));
        const fd = new FormData(); fd.append("file", f); if (acctSel.value) fd.append("account_id", acctSel.value);
        try { renderPreview(area, await api("/api/statements/parse", { method: "POST", form: fd }), f.name); }
        catch (e) { area.innerHTML = ""; area.append(notices([e.message], "bad")); }
      })), area);
  const hist = h("div", { class: "card" }, h("h2", {}, "Imported statements"), working("Loading…"));
  root.append(hist);
  const list = await api("/api/statements");
  hist.lastChild.remove();
  hist.append(list.length ? table(["File", "Account", "Period", "Transactions", "Imported", ""], list.map((i) => h("tr", {},
    h("td", { class: "t-main" }, i.filename), h("td", {}, i.account || "–"), h("td", {}, `${i.first || ""} – ${i.last || ""}`), h("td", { class: "num" }, i.n),
    h("td", { class: "small muted" }, i.created_at.replace("T", " ")),
    h("td", {}, i.status !== "imported" ? h("span", { class: "badge warn", title: "Uploaded and previewed, but never imported" }, "not imported") : null, " ",
      h("button", { class: "btn sm danger", onclick: async () => {
        const msg = i.status === "imported"
          ? `Delete the statement "${i.filename}" and its ${i.n} transactions?\n\nReceipts and invoices matched to those transactions are kept, but unlinked. This can't be undone - you can import the file again later.`
          : `Delete the unfinished upload "${i.filename}"?`;
        if (!confirm(msg)) return;
        const r = await api(`/api/statements/${i.id}`, { method: "DELETE" }); toast(`Statement deleted (${r.deleted_transactions} transactions)`); await refreshMeta(); go("statements");
      } }, "Delete"))))) : h("p", { class: "empty" }, "Nothing imported yet."));
};

function renderPreview(area, res, fname) {
  area.innerHTML = "";
  const rows = res.transactions.map((t) => ({ ...t, include: !t.duplicate }));
  const m = res.meta, st = m.statement || {};
  const acct = { id: m.account_id || "", name: m.account_name || fname.replace(/\.[^.]+$/, ""), iban: m.account_iban || "", currency: m.currency || cur() };
  const ins = rows.filter((r) => r.include);
  const sumIn = ins.filter((r) => r.amount > 0).reduce((a, r) => a + r.amount, 0), sumOut = ins.filter((r) => r.amount < 0).reduce((a, r) => a + r.amount, 0);
  const acctSel = h("select", { onchange: (e) => { acct.id = e.target.value; newBox.style.display = acct.id ? "none" : ""; } },
    h("option", { value: "" }, "Create new account"), S.accounts.map((a) => h("option", { value: a.id, selected: String(a.id) === String(acct.id) }, a.name)));
  const newBox = h("div", { class: "form", style: acct.id ? "display:none" : "" },
    h("label", { class: "f" }, "New account name", h("input", { value: acct.name, oninput: (e) => (acct.name = e.target.value) })),
    h("label", { class: "f" }, "IBAN / number", h("input", { value: acct.iban, oninput: (e) => (acct.iban = e.target.value) })),
    h("label", { class: "f" }, "Currency", h("input", { value: acct.currency, oninput: (e) => (acct.currency = e.target.value.toUpperCase()) })));
  const balance = st.balance_check ? h("span", { class: "badge " + (st.balance_check === "ok" ? "ok" : "bad") }, st.balance_check === "ok" ? "Balances reconcile" : "Balance " + st.balance_check) : null;
  const countEl = h("strong");
  const remember = h("input", { type: "checkbox", checked: true });
  const update = () => { countEl.textContent = rows.filter((r) => r.include).length; };
  const tbody = rows.map((r) => {
    const tr = h("tr", { class: r.duplicate ? "dup" : "" },
      h("td", {}, h("input", { type: "checkbox", checked: r.include, onchange: (e) => { r.include = e.target.checked; update(); } })),
      h("td", { class: "num" }, r.date), h("td", { class: "t-main" }, r.counterparty || r.description), h("td", { class: "desc" }, r.counterparty ? r.description : ""),
      h("td", { class: "t-amt" }, amt(r.amount), fxLine(r.currency, r.amount_base, r.orig_amount, r.orig_currency)), h("td", { class: "small muted t-hide" }, r.currency),
      h("td", {}, catSelect(r.category, (v) => { r.category = v; r.category_source = "user"; r.remember = remember.checked; }),
        r.category_source && r.category_source !== "user" ? h("div", { class: "small muted" }, { rule: "learned rule", keyword: "keyword", llm: "model" }[r.category_source] || r.category_source) : null,
        r.duplicate ? h("span", { class: "badge" }, "already imported") : null));
    return tr;
  });
  update();
  area.append(h("div", { class: "card" },
    h("div", { class: "row" }, h("h2", { style: "margin:0" }, `Preview: ${fname}`), balance, h("span", { class: "spacer" }), h("span", { class: "badge info" }, m.method)),
    h("p", { class: "muted small" }, `${rows.length} lines · money in ${money(sumIn)} · money out ${money(sumOut)}`,
      st.period_start ? ` · statement period ${st.period_start} – ${st.period_end || "?"}` : "",
      st.opening_balance != null ? ` · opening ${money(st.opening_balance)} → closing ${money(st.closing_balance)}` : ""),
    notices(res.warnings),
    m.model_notes ? h("div", { class: "notice info" }, h("div", {}, h("strong", {}, "Model notes: "), m.model_notes)) : null,
    h("div", { class: "row", style: "margin:10px 0" }, h("label", { class: "f" }, "Import into", acctSel)), newBox,
    h("div", { style: "margin:12px 0" }, table(["Import", "Date", "Counterparty", "Description", "Amount", "", "Category"], tbody)),
    h("div", { class: "row end" }, h("label", { class: "chk small" }, remember, "Remember my category changes for these counterparties"), h("span", { class: "spacer" }),
      h("button", { class: "btn", onclick: () => go("statements") }, "Cancel"),
      h("button", { class: "btn primary", onclick: async (e) => {
        e.target.disabled = true;
        const body = { import_id: res.import_id, account_id: acct.id ? Number(acct.id) : null, rows: rows.filter((r) => r.include) };
        if (!acct.id) body.new_account = { name: acct.name, iban: acct.iban, currency: acct.currency };
        try {
          const out = await api("/api/statements/commit", { method: "POST", body });
          toast(`Imported ${out.inserted} transactions` + (out.skipped ? `, ${out.skipped} duplicates skipped` : "") + (out.matched_documents ? `, ${out.matched_documents} receipts matched` : ""));
          await refreshMeta(); go("statements");
        } catch (err) { toast(err.message, true); e.target.disabled = false; }
      } }, "Import ", countEl, " transactions"))));
}

// ---------- receipts
R.receipts = async (root) => {
  root.append(h("h1", {}, "Receipts"), h("p", { class: "sub" }, "Drop photos or PDFs of receipts. The model reads merchant, date, total and VAT; each receipt is matched to its bank transaction automatically."));
  const queue = h("div");
  const queueCard = h("div", { class: "card", style: "display:none" });
  const listCard = h("div", { class: "card" });
  const detail = h("div");
  root.append(h("div", { class: "card" }, dropzone("Drop receipts here", "Photos (JPG/PNG) or PDFs - several at once is fine", "image/*,.pdf", true, async (files) => {
    for (const f of files) {
      const line = h("div", { class: "working" }, h("span", { class: "spin" }), `Reading ${f.name}…`);
      queue.prepend(line);
      const fd = new FormData(); fd.append("file", f);
      try {
        const r = await api("/api/receipts/upload", { method: "POST", form: fd });
        if (r.queued) {
          line.replaceWith(h("div", { class: "notice warn" }, h("div", {}, h("strong", {}, `${f.name}: `),
            `saved in the queue - no model is available right now, it is read automatically when the model is back (${r.waiting} waiting).`)));
          loadQueue();
          continue;
        }
        const rc = r.receipt;
        line.replaceWith(h("div", { class: "notice " + (r.warnings.length ? "warn" : "ok") },
          h("div", {}, h("strong", {}, `${f.name}: `), `${rc.merchant || "?"} · ${rc.date || "no date"} · ${money(rc.total, rc.currency)}`,
            r.cash ? " · paid in cash, booked in the cash book ✓" : rc.transaction_id ? " · matched to bank ✓" : "", r.warnings.length ? h("ul", {}, r.warnings.map((w) => h("li", {}, w))) : null,
            r.document_type === "invoice" ? h("div", {}, "This looks like an invoice. ", h("button", { class: "btn sm", onclick: async () => { await api(`/api/receipts/${rc.id}/to-invoice`, { method: "POST" }); toast("Moved to invoices"); loadList(); } }, "Move to Invoices")) : null)));
        loadList();
      } catch (e) { line.replaceWith(h("div", { class: "notice bad" }, `${f.name}: ${e.message}`)); }
    }
  }), queue), queueCard, emailCard(openDoc), h("div", { class: "split" }, listCard, detail));

  // receipts uploaded while no model was available; read by the server once the model is back
  let queueTimer = null;
  async function loadQueue() {
    const q = await api("/api/receipts/queue");
    const open = q.items.filter((x) => x.status !== "done"), done = q.items.filter((x) => x.status === "done");
    queueCard.innerHTML = "";
    queueCard.style.display = q.items.length ? "" : "none";
    if (!q.items.length) return;
    const waiting = open.filter((x) => x.status !== "error").length;
    queueCard.append(h("div", { class: "row" }, h("h2", { style: "margin:0" }, `Waiting for the model (${waiting})`),
      q.running ? h("span", { class: "row small" }, h("span", { class: "spin" }), "Reading…") : null, h("span", { class: "spacer" }),
      waiting ? h("button", { class: "btn sm primary", disabled: q.running, onclick: async (e) => {
        e.target.disabled = true; e.target.textContent = "Reading…";
        try {
          const r = await api("/api/receipts/queue/process", { method: "POST" });
          if (r.offline) toast("Model still offline - receipts stay in the queue", true);
          else toast(`Read ${r.done} receipt(s)` + (r.errors ? `, ${r.errors} failed` : "") + (r.waiting ? `, ${r.waiting} still waiting` : ""));
        } catch (err) { toast(err.message, true); }
        loadQueue(); loadList();
      } }, "Try now") : null),
      h("p", { class: "muted small" }, waiting ? "Uploaded while no model was available. The server checks every minute and reads them as soon as the model is back - you can close this page."
        : "Recently read from the queue."),
      table(["", "File", "Uploaded", "Status", ""], [...open, ...done.slice(-10).reverse()].map((x) => h("tr", {},
        h("td", {}, isImg(x.file_path) ? h("a", { href: fileUrl(x.file_path), target: "_blank" }, h("img", { class: "thumb", src: fileUrl(x.file_path), loading: "lazy", alt: "" })) : h("a", { class: "btn sm", href: fileUrl(x.file_path), target: "_blank" }, "File")),
        h("td", { class: "t-main" }, x.original_name || "–"), h("td", { class: "small muted" }, (x.created_at || "").replace("T", " ").slice(0, 16)),
        h("td", { class: "t-wide" }, h("span", { class: "badge " + ({ waiting: "warn", reading: "info", error: "bad", done: "ok" }[x.status] || "") },
            { waiting: "waiting", reading: "reading", error: "failed", done: "read" }[x.status] || x.status),
          x.status === "done" && x.document_type === "invoice" ? h("span", { class: "badge", title: "Open it and press \"It's an invoice\" to move it" }, "looks like an invoice") : null,
          x.status === "error" || (x.status === "waiting" && x.attempts) ? h("div", { class: "small muted" }, friendly(x.reason)) : null),
        h("td", { class: "row t-act" },
          x.status === "done" && x.receipt_id ? h("button", { class: "btn sm", onclick: () => openDoc("receipt", x.receipt_id) }, "Open") : null,
          x.status === "error" ? h("button", { class: "btn sm", onclick: async () => { await api(`/api/receipts/queue/${x.id}/retry`, { method: "POST" }); loadQueue(); } }, "Retry") : null,
          x.status !== "reading" ? h("button", { class: "btn sm ghost", title: x.status === "done" ? "Remove from this list (the receipt stays)" : "Remove from the queue and delete the file",
            onclick: async () => {
              if (x.status !== "done" && !confirm(`Remove "${x.original_name}" from the queue? The uploaded file is deleted.`)) return;
              await api(`/api/receipts/queue/${x.id}`, { method: "DELETE" }); loadQueue();
            } }, "✕") : null)))));
    // keep it current while the tab is open: items disappear into the receipt list as they are read
    clearTimeout(queueTimer);
    if (waiting && document.body.contains(queueCard)) queueTimer = setTimeout(() => { loadQueue(); if (!open.length) loadList(); }, 20000);
  }

  let selected = null;
  // "Open" in the email list: show that receipt here, or jump to the invoice
  async function openDoc(type, id) {
    if (type === "invoice") { S.focusInvoice = id; return go("invoices"); }
    selected = id;
    try { await showDetail(id); } catch (e) { detail.innerHTML = ""; return toast("That receipt no longer exists (deleted?)", true); }
    loadList();
    detail.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  async function loadList() {
    const rows = await api("/api/receipts");
    listCard.innerHTML = "";
    listCard.append(h("div", { class: "row" }, h("h2", { style: "margin:0" }, `All receipts (${rows.length})`), h("span", { class: "spacer" }),
      h("span", { class: "small muted" }, `${rows.filter((r) => r.status === "review").length} to review`)));
    if (!rows.length) { listCard.append(h("p", { class: "empty" }, "No receipts yet.")); return; }
    listCard.append(h("div", { style: "margin-top:10px" }, table(["", "Date", "Merchant", "Total", "Category", "Status"], rows.map((r) => h("tr", { class: "click" + (selected === r.id ? " sel" : ""), onclick: async () => { selected = r.id; loadList(); await showDetail(r.id); if (narrow()) detail.scrollIntoView({ behavior: "smooth", block: "start" }); } },
      h("td", { class: "t-hide" }, isImg(r.file_path) ? h("img", { class: "thumb", src: fileUrl(r.file_path), loading: "lazy", alt: "" }) : h("div", { class: "thumb" })),
      h("td", { class: "num" }, r.date || "?"), h("td", { class: "t-main" }, r.merchant || "?"), h("td", { class: "num t-amt" }, money(r.total, r.currency), r.currency !== cur() && r.total_base != null ? h("div", { class: "small muted" }, "≈ " + money(r.total_base, cur())) : null),
      h("td", { class: "small" }, r.category),
      h("td", {}, h("span", { class: "badge " + (r.status === "confirmed" ? "ok" : "warn") }, r.status === "confirmed" ? "confirmed" : "review"), " ",
        r.transaction_id ? (r.tx_account_kind === "cash" ? h("span", { class: "badge info", title: "Booked in the cash book" }, "cash book") : h("span", { class: "badge info", title: `Bank ${r.tx_date} ${money(r.tx_amount)}` }, "matched")) : null))))));
  }

  async function showDetail(id) {
    detail.innerHTML = ""; detail.append(working("Loading…"));
    const r = await api(`/api/receipts/${id}`);
    const f = { ...r };
    const field = (label, key, type = "text") => h("label", { class: "f" }, label, h("input", { type, class: type === "number" ? "amount" : null, step: type === "number" ? "0.01" : null, value: f[key] ?? "", oninput: (e) => (f[key] = type === "number" ? (e.target.value === "" ? null : Number(e.target.value)) : e.target.value) }));
    detail.innerHTML = "";
    detail.append(h("div", { class: "card" },
      h("div", { class: "row" }, h("h2", { style: "margin:0" }, r.merchant || "Receipt"), r.confidence ? h("span", { class: "badge " + (r.confidence === "high" ? "ok" : r.confidence === "low" ? "bad" : "warn") }, "confidence: " + r.confidence) : null),
      r.file_path ? (isImg(r.file_path) ? h("a", { href: fileUrl(r.file_path), target: "_blank" }, h("img", { class: "docview", src: fileUrl(r.file_path), alt: "Receipt image", style: "margin:10px 0" }))
        : h("p", {}, h("a", { href: fileUrl(r.file_path), target: "_blank" }, "Open original file"))) : null,
      notices((r.raw && r.raw.warnings) || []),
      h("div", { class: "form" }, field("Date", "date", "date"), field("Merchant", "merchant"), field("Total", "total", "number"), field("Net", "net", "number"),
        field("VAT", "vat", "number"), field("Currency", "currency"), field("Card (last 4 digits)", "card_last4"),
        h("label", { class: "f" }, "Category", catSelect(f.category, (v) => (f.category = v))),
        h("label", { class: "f" }, "Payment", h("select", { onchange: (e) => (f.payment_method = e.target.value) }, ["card", "cash", "transfer", "mobile", "other", "unknown"].map((p) => h("option", { selected: p === f.payment_method }, p))))),
      (r.currency || cur()) !== cur() ? h("div", { class: "notice info" }, h("div", {},
        r.total_base != null ? `= ${money(r.total_base, cur())} at ${r.fx_rate} ${cur()}/${r.currency} (${r.fx_source === "manual" ? "manual rate" : "ECB rate for " + r.date}). ` : `No exchange rate found for ${r.currency}. `,
        h("label", { class: "chk" }, "Own rate:", h("input", { type: "number", step: "0.0001", style: "width:110px", placeholder: "e.g. 7.4600", value: r.fx_source === "manual" ? r.fx_rate : "", oninput: (e) => (f.fx_rate = e.target.value === "" ? 0 : Number(e.target.value)) })))) : null,
      h("label", { class: "f", style: "margin-top:10px" }, "Notes", h("input", { value: f.notes || "", oninput: (e) => (f.notes = e.target.value) })),
      r.vat_lines.length ? h("div", {}, h("h3", {}, "VAT"), table(["Rate", "Net", "VAT", "Gross"], r.vat_lines.map((v) => h("tr", {}, h("td", {}, v.rate != null ? v.rate + "%" : "?"), h("td", { class: "num" }, money(v.net)), h("td", { class: "num" }, money(v.vat)), h("td", { class: "num" }, money(v.gross)))))) : null,
      r.raw && r.raw.ocr_text ? h("details", {}, h("summary", { class: "small muted", style: "margin-top:10px;cursor:pointer" }, "OCR transcription"), h("pre", { class: "mono", style: "white-space:pre-wrap;background:var(--surface-2);padding:8px;border-radius:6px" }, r.raw.ocr_text)) : null,
      r.items.length ? h("details", {}, h("summary", { class: "small muted", style: "margin-top:10px;cursor:pointer" }, `${r.items.length} line items`), table(["Item", "Qty", "Total"], r.items.map((v) => h("tr", {}, h("td", {}, v.description), h("td", { class: "num" }, v.quantity ?? ""), h("td", { class: "num" }, money(v.total)))))) : null,
      h("h3", {}, "Payment"),
      r.card_last4 ? (r.card_account ? h("p", { class: "small" }, `Paid with card ••${r.card_last4} → ${r.card_account}`)
        : h("div", { class: "notice info" }, h("div", {}, `Card ••${r.card_last4} isn't linked to an account yet. `, linkCardControl(r.card_last4, null, () => { showDetail(id); loadList(); })))) : null,
      r.transaction_id && r.tx_account_kind === "cash" ? h("p", { class: "small" }, `Paid in cash - booked in the cash book on ${r.tx_date}: ${money(r.tx_amount, r.currency)}. Change the payment method to remove it from the cash book.`) :
      r.transaction_id ? h("div", { class: "row" }, h("span", {}, `${r.tx_date} · ${money(r.tx_amount)} · `, h("span", { class: "muted" }, r.tx_description)), h("button", { class: "btn sm", onclick: async () => { await api("/api/reconcile/link", { method: "POST", body: { doc_type: "receipt", doc_id: r.id, transaction_id: null } }); showDetail(id); loadList(); } }, "Unlink"))
        : h("p", { class: "muted small" }, (r.why_unmatched ? "Not matched: " + r.why_unmatched + ". " : "Not matched to a bank transaction yet. ") + "Paid in cash? Set Payment to cash and save - it goes into the cash book."),
      h("div", { class: "row end", style: "margin-top:14px" },
        h("button", { class: "btn danger", onclick: async () => { if (!confirm("Delete this receipt?")) return; await api(`/api/receipts/${id}`, { method: "DELETE" }); detail.innerHTML = ""; loadList(); } }, "Delete"),
        h("button", { class: "btn", onclick: async () => { await api(`/api/receipts/${id}/to-invoice`, { method: "POST" }); toast("Moved to invoices"); detail.innerHTML = ""; loadList(); } }, "It's an invoice"),
        h("button", { class: "btn primary", onclick: async () => {
          const body = { date: f.date, merchant: f.merchant, total: f.total, net: f.net, vat: f.vat, currency: (f.currency || "").toUpperCase(), category: f.category, payment_method: f.payment_method, card_last4: f.card_last4 || "", notes: f.notes, status: "confirmed" };
          if (f.fx_rate !== r.fx_rate) body.fx_rate = f.fx_rate;
          await api(`/api/receipts/${id}`, { method: "PATCH", body }); toast("Receipt saved"); showDetail(id); loadList();
        } }, "Confirm & save"))));
  }
  loadQueue().catch(() => {});
  await loadList();
};

// ---------- payment cards (last 4 digits) per account
function linkCardControl(last4, suggestedId, after) {
  const banks = S.accounts.filter((a) => a.kind !== "cash");
  const sel = h("select", {}, h("option", { value: "" }, "Choose account…"), banks.map((a) => h("option", { value: a.id, selected: a.id === suggestedId }, a.name)));
  const label = h("input", { placeholder: "Label, e.g. Visa or Apple Pay", size: 22 });
  return h("span", { class: "row", style: "display:inline-flex" }, sel, label, h("button", { class: "btn sm primary", onclick: async () => {
    if (!sel.value) return toast("Choose the account this card belongs to", true);
    const r = await api("/api/cards", { method: "POST", body: { account_id: Number(sel.value), last4, label: label.value } });
    toast(`Card ••${last4} linked` + (r.matched ? ` - ${r.matched} receipt(s) matched` : "")); await refreshMeta(); after && after();
  } }, "Link"));
}

function ignoredCardsLine() {
  const box = h("div", { class: "small muted", style: "margin-top:10px" });
  api("/api/cards").then((d) => {
    if (!d.ignored.length) return;
    box.append("Ignored cards (not on any of your accounts, never matched): ", ...d.ignored.map((k) => h("span", { class: "chip" }, `••${k.last4} · ${k.receipts} receipt(s) `,
      h("button", { class: "btn sm ghost", style: "padding:0 2px", title: "Stop ignoring", onclick: async () => { await api(`/api/cards/ignore/${k.last4}`, { method: "DELETE" }); go("settings"); } }, "undo"))));
  });
  return box;
}

function cardCell(a) {
  const l4 = h("input", { placeholder: "last 4", size: 5, maxlength: 19, inputmode: "numeric" });
  const label = h("input", { placeholder: "label", size: 10 });
  return h("div", {}, h("div", { class: "chips", style: "margin-bottom:4px" }, (a.cards || []).map((k) => h("span", { class: "chip", title: k.label || "" }, "••" + k.last4, k.label ? " " + k.label : "", " ",
      h("button", { class: "btn sm ghost", style: "padding:0 2px", title: "Remove card", onclick: async () => { await api(`/api/cards/${k.id}`, { method: "DELETE" }); await refreshMeta(); go("settings"); } }, "✕")))),
    h("span", { class: "row", style: "display:inline-flex" }, l4, label, h("button", { class: "btn sm", onclick: async () => {
      try { await api("/api/cards", { method: "POST", body: { account_id: a.id, last4: l4.value, label: label.value } }); await refreshMeta(); go("settings"); }
      catch (e) { toast(e.message, true); }
    } }, "Add")));
}

// ---------- desktop automation: wake the GPU PC, load the model, do the model work, power it off
const runBadge = { ok: "ok", "nothing to do": "", running: "info", error: "bad", timeout: "warn", cancelled: "warn" };
function desktopCard() {
  const ds = {};
  const keys = ["desktop_auto", "desktop_every_days", "desktop_time", "desktop_model", "desktop_only_if_work", "desktop_categorize",
    "desktop_shutdown", "desktop_respect_users", "desktop_boot_wait_min", "desktop_max_run_min", "desktop_mac", "desktop_hosts"];
  keys.forEach((k) => (ds[k] = S.settings[k] ?? ""));
  const inp = (label, key, attrs = {}) => h("label", { class: "f" }, label, h("input", { value: ds[key], oninput: (e) => (ds[key] = e.target.value), ...attrs }));
  const chk = (label, key) => h("label", { class: "chk" }, h("input", { type: "checkbox", checked: ds[key] === "1", onchange: (e) => (ds[key] = e.target.checked ? "1" : "0") }), label);
  const models = (S.status?.llm?.available || []);
  const statusBox = h("div"), testOut = h("div"), runsBox = h("div");
  let timer = null;
  const fmt = (t) => (t || "").replace("T", " ").slice(0, 16);

  async function refresh() {
    let d;
    try { d = await api("/api/desktop"); } catch (e) { statusBox.innerHTML = ""; statusBox.append(notices([e.message], "bad")); return; }
    statusBox.innerHTML = "";
    const last = d.runs[0];
    const countingDown = last && (last.shutdown || "").startsWith("in 2 minutes") && Date.now() - new Date(last.finished_at) < 150000;
    statusBox.append(h("div", { class: "row", style: "margin:4px 0 10px" },
      h("span", { class: "badge " + (S.settings.desktop_auto === "1" ? "ok" : "") }, S.settings.desktop_auto === "1" ? "on" : "off"),
      d.running ? h("span", { class: "row small" }, h("span", { class: "spin" }), d.phase || "Working…")
        : h("span", { class: "small muted" }, d.next_run ? "Next run " + fmt(d.next_run) : "Not scheduled"),
      h("span", { class: "spacer" }),
      countingDown ? h("button", { class: "btn sm", title: "Cancel the power-off that is counting down on the PC", onclick: async () => {
        try { await api("/api/desktop/keep-on", { method: "POST" }); toast("The PC stays on"); } catch (e) { toast(e.message, true); }
      } }, "Keep the PC on") : null,
      d.running ? h("button", { class: "btn sm", onclick: async () => { await api("/api/desktop/cancel", { method: "POST" }); toast("Stopping after the current step"); } }, "Stop")
        : h("button", { class: "btn sm primary", onclick: async (e) => {
          e.target.disabled = true;
          try { await api("/api/desktop/run", { method: "POST" }); toast("Run started"); } catch (err) { toast(err.message, true); }
          refresh();
        } }, "Run now")));
    runsBox.innerHTML = "";
    if (d.runs.length) runsBox.append(table(["Started", "", "Result", "Waiting", "Done", "Power-off", ""], d.runs.map((r) => h("tr", {},
      h("td", { class: "small" }, fmt(r.started_at)), h("td", { class: "small muted" }, r.trigger),
      h("td", {}, h("span", { class: "badge " + (runBadge[r.status] ?? "warn") }, r.status), r.woke ? h("div", { class: "small muted" }, "woke the PC") : null),
      h("td", { class: "small" }, Object.entries(r.work || {}).map(([k, v]) => `${v} ${k}`).join(", ")),
      h("td", { class: "small" }, [r.done?.uploads ? `${r.done.uploads.done} uploads` : "", r.done?.emails ? `${r.done.emails.imported} email docs` : "",
        r.done?.categorize ? `${r.done.categorize.categorized}/${r.done.categorize.checked} categorized` : ""].filter(Boolean).join(", ")),
      h("td", { class: "small" }, r.shutdown || ""),
      h("td", {}, h("details", {}, h("summary", { class: "small muted", style: "cursor:pointer" }, "log"), h("pre", { class: "mono small", style: "white-space:pre-wrap;max-width:520px" }, r.log)))))));
    clearTimeout(timer);
    if (d.running && document.body.contains(card)) timer = setTimeout(refresh, 4000);
  }

  const scriptPre = h("pre", { class: "mono small", style: "white-space:pre;overflow:auto;max-height:340px;background:var(--surface-2);padding:10px;border-radius:8px" }, "…");
  let script = "";
  const setup = h("details", { ontoggle: async (e) => {
    if (!e.target.open || script) return;
    try { script = (await api("/api/desktop/setup-script")).script; scriptPre.textContent = script; } catch (err) { scriptPre.textContent = err.message; }
  } }, h("summary", { style: "cursor:pointer;font-weight:600;margin-top:12px" }, "One-time setup on the desktop (needed to power it off)"),
    h("p", { class: "muted small" }, "Powering the PC off works over SSH with a key that can only ask the PC for its status and power it off. Save this as ledger-desktop-setup.sh on the desktop's Linux and run it once with sudo bash ledger-desktop-setup.sh. Then press Test."),
    h("div", { class: "row", style: "margin:6px 0" },
      h("button", { class: "btn sm", onclick: async () => { try { await navigator.clipboard.writeText(script); toast("Copied"); } catch { toast("Copy blocked - use Download", true); } } }, "Copy"),
      h("button", { class: "btn sm", onclick: () => { const a = h("a", { href: URL.createObjectURL(new Blob([script], { type: "text/x-sh" })), download: "ledger-desktop-setup.sh" }); a.click(); } }, "Download")),
    scriptPre);

  const card = h("div", { class: "card" }, h("h2", {}, "Desktop automation"),
    h("p", { class: "muted small" }, "For a model that runs on a PC you don't keep on: every few days the server wakes that PC over the LAN, waits for the model server (and loads the model if it is Unsloth Studio), reads queued receipt uploads and email documents, categorizes new transactions, and powers the desktop off again. It only powers it off when it was the one that woke it, and never while you are using it - and the PC shows a 2-minute warning first."),
    statusBox,
    h("div", { class: "stack" }, chk("Run automatically", "desktop_auto")),
    h("div", { class: "form", style: "margin-top:10px" },
      inp("Every (days)", "desktop_every_days", { type: "number", min: 1, max: 60 }), inp("At (time)", "desktop_time", { type: "time" }),
      h("label", { class: "f" }, "Model to load", h("input", { value: ds.desktop_model, list: "desk-models", oninput: (e) => (ds.desktop_model = e.target.value) }),
        h("datalist", { id: "desk-models" }, models.map((m) => h("option", { value: m })))),
      inp("Wait for the PC to start (min)", "desktop_boot_wait_min", { type: "number", min: 1 }),
      inp("Give up after (min)", "desktop_max_run_min", { type: "number", min: 5 }),
      inp("Wake-on-LAN MAC address", "desktop_mac"),
      inp("Desktop addresses (Linux: tailnet, LAN)", "desktop_hosts")),
    h("div", { style: "margin-top:10px;display:grid;gap:6px;justify-items:start" },
      chk("Only wake the PC when something is waiting for the model", "desktop_only_if_work"),
      chk("Also categorize new Uncategorized transactions", "desktop_categorize"),
      chk("Power the PC off when finished (only if this automation woke it)", "desktop_shutdown"),
      chk("…but not while someone is using it", "desktop_respect_users")),
    h("div", { class: "row", style: "margin-top:12px" },
      h("button", { class: "btn primary", onclick: async () => { S.settings = await api("/api/settings", { method: "PUT", body: ds }); toast("Saved"); refresh(); } }, "Save"),
      h("button", { class: "btn", onclick: async (e) => {
        e.target.disabled = true; testOut.innerHTML = ""; testOut.append(working("Checking the PC…"));
        try {
          const t = await api("/api/desktop/test", { method: "POST" });
          const st = t.status || {};
          const ok = [], bad = [];
          (t.studio ? ok : bad).push(t.studio ? "The model server answers" + (t.loaded.length ? ` (loaded: ${t.loaded.join(", ")})` : " (no model loaded)") : (t.studio_error || "The model server does not answer") + (t.windows ? " - the PC is running Windows" : t.machine_up ? "" : " - the PC seems to be off"));
          (t.ssh ? ok : bad).push(t.ssh ? `Power-off access works (Linux up since ${st.boot}${st.in_use ? ", someone is using it right now" : ""})` : `Power-off access (SSH) not working: ${t.ssh_error}`);
          testOut.innerHTML = ""; if (ok.length) testOut.append(notices(ok, "ok")); if (bad.length) testOut.append(notices(bad, "bad"));
        } catch (err) { testOut.innerHTML = ""; testOut.append(notices([err.message], "bad")); }
        e.target.disabled = false;
      } }, "Test")),
    testOut, setup, h("h3", {}, "Recent runs"), runsBox);
  refresh();
  return card;
}

// ---------- email inboxes
const statusBadge = { imported: "ok", skipped: "", error: "bad", pending: "warn" };
function emailCard(onOpen) {
  const card = h("details", { class: "card", open: store.get("emailOpen", true), ontoggle: (e) => store.set("emailOpen", e.target.open) });
  const body = h("div");
  card.append(h("summary", { style: "cursor:pointer;font-weight:600" }, "Receipts from your email"), body);
  let timer = null;
  async function draw() {
    const st = await api("/api/email/status");
    body.innerHTML = "";
    if (!st.accounts.length) {
      body.append(h("p", { class: "muted small", style: "margin-top:8px" }, "No email inbox connected yet. Add one under Settings → Email inboxes, and receipts, invoices and order confirmations are picked up from your mail automatically."),
        h("button", { class: "btn", onclick: () => go("settings") }, "Connect an inbox"));
      return;
    }
    const s = st.state, n = st.counts;
    const last = st.accounts.map((a) => `${a.name}: ${a.last_scan_at ? a.last_scan_at.replace("T", " ").slice(0, 16) : "never scanned"}${a.last_status && a.last_status.startsWith("error") ? " (" + a.last_status + ")" : ""}`).join(" · ");
    body.append(h("div", { class: "row", style: "margin:8px 0" },
      s.running ? h("span", { class: "row" }, h("span", { class: "spin" }), h("span", { class: "small" }, s.phase || "Working…")) : h("span", { class: "small muted" }, "Last scan - " + last),
      h("span", { class: "spacer" }),
      h("span", { class: "badge ok" }, `${n.imported || 0} imported`), h("span", { class: "badge warn" }, `${n.pending || 0} waiting`),
      h("span", { class: "badge" }, `${n.skipped || 0} skipped`), n.error ? h("span", { class: "badge bad" }, `${n.error} errors`) : null,
      h("button", { class: "btn primary", disabled: s.running, onclick: async () => { await api("/api/email/scan", { method: "POST", body: {} }); draw(); } }, "Scan now")));
    if (st.scan_hours && Number(st.scan_hours) > 0) body.append(h("p", { class: "muted small" }, `Scans automatically every ${st.scan_hours} h. Emails are never marked as read, moved or deleted.`));
    const items = await api("/api/email/items?limit=40");
    if (items.length) body.append(table(["Sent", "From", "Subject / attachment", "Result", ""], items.map((it) => h("tr", {},
      h("td", { class: "num" }, it.sent_at || ""), h("td", { class: "small" }, (it.sender || "").replace(/<.*>/, "").trim() || it.sender),
      h("td", { class: "t-main" }, it.subject, it.part && !["body", "-"].includes(it.part) ? h("div", { class: "desc" }, "📎 " + it.part) : null),
      h("td", {}, h("span", { class: "badge " + (statusBadge[it.status] || "") }, it.status === "imported" ? (it.doc_type === "invoice" ? "invoice" : "receipt") + " #" + it.doc_id : it.status),
        it.reason ? h("div", { class: "small muted" }, friendly(it.reason)) : null),
      h("td", { class: "row t-act" }, it.status === "imported" ? h("button", { class: "btn sm", onclick: () => onOpen(it.doc_type, it.doc_id) }, "Open") : null,
        ["skipped", "error"].includes(it.status) ? h("button", { class: "btn sm", onclick: async () => { await api(`/api/email/items/${it.id}/retry`, { method: "POST" }); draw(); } }, "Retry") : null,
        it.status === "pending" ? h("button", { class: "btn sm ghost", onclick: async () => { await api(`/api/email/items/${it.id}/skip`, { method: "POST" }); draw(); } }, "Skip") : null)))));
    clearTimeout(timer);
    if (s.running && document.body.contains(card)) timer = setTimeout(draw, 3000);
  }
  draw().catch((e) => body.append(notices([e.message], "bad")));
  return card;
}

async function emailSettingsCard() {
  const presets = await api("/api/email/presets");
  const accounts = await api("/api/email/accounts");
  const card = h("div", { class: "card" }, h("h2", {}, "Email inboxes"),
    h("p", { class: "muted small" }, "The app looks through new emails for receipts, invoices and order confirmations (PDF attachments, photos, or the email itself), reads them with your model and adds them to Receipts or Invoices. Read-only: nothing is marked as read, moved or deleted. Outlook/Hotmail are not supported yet (Microsoft only allows OAuth sign-in)."));
  const out = h("div");
  const hoursSel = h("select", { onchange: async (e) => { await api("/api/settings", { method: "PUT", body: { email_scan_hours: e.target.value } }); toast("Saved"); } },
    [["0", "Only when I press Scan now"], ["1", "Every hour"], ["3", "Every 3 hours"], ["6", "Every 6 hours"], ["12", "Every 12 hours"], ["24", "Once a day"]]
      .map(([v, l]) => h("option", { value: v, selected: String(S.settings.email_scan_hours ?? "6") === v }, l)));
  card.append(h("div", { class: "row", style: "margin-bottom:10px" }, h("label", { class: "f" }, "Scan", hoursSel)));
  if (accounts.length) card.append(table(["Inbox", "Folders", "Last scan", ""], accounts.map((a) => h("tr", {},
    h("td", {}, h("strong", {}, a.name), h("div", { class: "small muted" }, `${a.username} · ${a.host}`)),
    h("td", {}, h("input", { value: a.folders, size: 18, onchange: async (e) => { await api(`/api/email/accounts/${a.id}`, { method: "PATCH", body: { folders: e.target.value } }); toast("Saved"); } })),
    h("td", { class: "small" }, a.last_scan_at ? a.last_scan_at.replace("T", " ").slice(0, 16) : "never", a.last_status ? h("div", { class: a.last_status.startsWith("error") ? "small" : "small muted", style: a.last_status.startsWith("error") ? "color:var(--critical)" : "" }, a.last_status) : null),
    h("td", { class: "row" },
      h("label", { class: "chk small" }, h("input", { type: "checkbox", checked: !!a.enabled, onchange: async (e) => { await api(`/api/email/accounts/${a.id}`, { method: "PATCH", body: { enabled: e.target.checked ? 1 : 0 } }); } }), "on"),
      h("button", { class: "btn sm", onclick: async (e) => {
        e.target.disabled = true; out.innerHTML = ""; out.append(working("Connecting…"));
        try { const r = await api(`/api/email/accounts/${a.id}/test`, { method: "POST" }); out.innerHTML = ""; out.append(notices([`Connected. Folders: ${r.folders.join(", ")}`], "ok")); }
        catch (err) { out.innerHTML = ""; out.append(notices([err.message], "bad")); }
        e.target.disabled = false;
      } }, "Test"),
      h("button", { class: "btn sm", onclick: async () => { await api("/api/email/scan", { method: "POST", body: { account_id: a.id } }); toast("Scanning - see the Receipts tab"); } }, "Scan now"),
      h("button", { class: "btn sm", onclick: async () => {
        const pw = prompt(`New app password for ${a.username}:`); if (!pw) return;
        await api(`/api/email/accounts/${a.id}`, { method: "PATCH", body: { password: pw } }); toast("Password updated");
      } }, "Password"),
      h("button", { class: "btn sm ghost", title: "Remove", onclick: async () => { if (confirm(`Disconnect ${a.name}? Receipts already found stay in your books.`)) { await api(`/api/email/accounts/${a.id}`, { method: "DELETE" }); go("settings"); } } }, "✕"))))));
  const na = { preset: "gmail", username: "", password: "", folders: "INBOX", since_days: 60, host: presets.gmail.host, port: presets.gmail.port, security: "ssl", name: "" };
  const help = h("p", { class: "muted small" }, presets.gmail.help);
  const hostIn = h("input", { value: na.host, oninput: (e) => (na.host = e.target.value) });
  const portIn = h("input", { type: "number", value: na.port, style: "width:90px", oninput: (e) => (na.port = Number(e.target.value)) });
  const custom = h("div", { class: "form", style: "display:none" }, h("label", { class: "f" }, "IMAP server", hostIn), h("label", { class: "f" }, "Port", portIn),
    h("label", { class: "f" }, "Security", h("select", { onchange: (e) => (na.security = e.target.value) }, [["ssl", "SSL/TLS (993)"], ["starttls", "STARTTLS (143)"], ["none", "None - local servers only"]].map(([v, l]) => h("option", { value: v }, l)))));
  card.append(h("h3", {}, "Connect an inbox"),
    h("div", { class: "form" },
      h("label", { class: "f" }, "Provider", h("select", { onchange: (e) => {
        na.preset = e.target.value; const p = presets[na.preset]; na.host = p.host; na.port = p.port; hostIn.value = p.host; portIn.value = p.port;
        help.textContent = p.help; custom.style.display = na.preset === "other" ? "" : "none";
      } }, Object.entries(presets).map(([k, p]) => h("option", { value: k }, p.label)))),
      h("label", { class: "f" }, "Email address", h("input", { type: "email", autocomplete: "off", oninput: (e) => (na.username = e.target.value) })),
      h("label", { class: "f" }, "App password", h("input", { type: "password", autocomplete: "new-password", oninput: (e) => (na.password = e.target.value) })),
      h("label", { class: "f" }, "Folders (comma separated)", h("input", { value: na.folders, oninput: (e) => (na.folders = e.target.value) })),
      h("label", { class: "f" }, "First scan looks back (days)", h("input", { type: "number", value: na.since_days, oninput: (e) => (na.since_days = Number(e.target.value)) }))),
    custom, help,
    h("div", { class: "row" }, h("button", { class: "btn primary", onclick: async (e) => {
      if (!na.username || !na.password) return toast("Email address and app password are needed", true);
      e.target.disabled = true; out.innerHTML = ""; out.append(working("Saving and connecting…"));
      try {
        const a = await api("/api/email/accounts", { method: "POST", body: { ...na, name: na.username } });
        try { await api(`/api/email/accounts/${a.id}/test`, { method: "POST" }); toast("Connected - first scan started"); await api("/api/email/scan", { method: "POST", body: { account_id: a.id } }); go("settings"); }
        catch (err) { out.innerHTML = ""; out.append(notices([`Saved, but the connection failed: ${err.message}`], "bad")); e.target.disabled = false; }
      } catch (err) { out.innerHTML = ""; out.append(notices([err.message], "bad")); e.target.disabled = false; }
    } }, "Connect")), out);
  return card;
}

// ---------- invoices
R.invoices = async (root) => {
  root.append(h("h1", {}, "Invoices"), h("p", { class: "sub" }, "Sales invoices you issued and purchase invoices you received. Paid status updates when the matching bank payment is imported."));
  const dirSel = h("select", {}, h("option", { value: "" }, "Detect automatically"), h("option", { value: "purchase" }, "Purchase (I pay)"), h("option", { value: "sales" }, "Sales (I get paid)"));
  const msg = h("div");
  const form = { direction: "sales", issue_date: today(), currency: cur(), status: "unpaid", category: "Sales revenue" };
  const inp = (label, key, type = "text") => h("label", { class: "f" }, label, h("input", { type, step: type === "number" ? "0.01" : null, value: form[key] ?? "", oninput: (e) => (form[key] = type === "number" ? Number(e.target.value) : e.target.value) }));
  const listBox = h("div", { class: "card" });
  root.append(h("div", { class: "grid g2" },
    h("div", { class: "card" }, h("h2", {}, "Upload an invoice"), h("div", { class: "row", style: "margin-bottom:10px" }, h("label", { class: "f" }, "Direction", dirSel)),
      dropzone("Drop invoice PDFs or photos", "Read by the model", ".pdf,image/*", true, async (files) => {
        for (const f of files) {
          msg.prepend(working(`Reading ${f.name}…`));
          const fd = new FormData(); fd.append("file", f); fd.append("direction", dirSel.value);
          try {
            const r = await api("/api/invoices/upload", { method: "POST", form: fd });
            msg.firstChild.replaceWith(h("div", { class: "notice " + (r.warnings.length ? "warn" : "ok") }, h("div", {}, h("strong", {}, f.name + ": "), `${r.invoice.direction} invoice ${r.invoice.number || ""} · ${r.invoice.counterparty} · ${money(r.invoice.total, r.invoice.currency)}`, r.warnings.length ? h("ul", {}, r.warnings.map((w) => h("li", {}, w))) : null)));
            load();
          } catch (e) { msg.firstChild.replaceWith(h("div", { class: "notice bad" }, `${f.name}: ${e.message}`)); }
        }
      }), msg),
    h("div", { class: "card" }, h("h2", {}, "Add manually"),
      h("div", { class: "form" },
        h("label", { class: "f" }, "Direction", h("select", { onchange: (e) => (form.direction = e.target.value) }, h("option", { value: "sales" }, "Sales"), h("option", { value: "purchase" }, "Purchase"))),
        inp("Number", "number"), inp("Customer / supplier", "counterparty"), inp("Issue date", "issue_date", "date"), inp("Due date", "due_date", "date"),
        inp("Net", "net", "number"), inp("VAT", "vat", "number"), inp("Total", "total", "number"), inp("Currency", "currency"),
        h("label", { class: "f" }, "Category", catSelect(form.category, (v) => (form.category = v)))),
      h("div", { class: "row end", style: "margin-top:12px" }, h("button", { class: "btn primary", onclick: async () => {
        if (!form.counterparty || !form.total) return toast("Counterparty and total are required", true);
        await api("/api/invoices", { method: "POST", body: form }); toast("Invoice added"); load();
      } }, "Add invoice")))), listBox);

  async function load() {
    const rows = await api("/api/invoices");
    const sum = (d) => rows.filter((r) => r.direction === d && r.status === "unpaid").reduce((a, r) => a + (r.total_base ?? 0), 0);
    listBox.innerHTML = "";
    listBox.append(h("div", { class: "row" }, h("h2", { style: "margin:0" }, "All invoices"), h("span", { class: "spacer" }),
      h("span", { class: "badge info" }, `Receivable ${money(sum("sales"), cur())}`), h("span", { class: "badge" }, `Payable ${money(sum("purchase"), cur())}`)));
    if (!rows.length) return listBox.append(h("p", { class: "empty" }, "No invoices yet."));
    listBox.append(h("div", { style: "margin-top:10px" }, table(["Type", "Number", "Counterparty", "Issued", "Due", "Total", "Status", ""], rows.map((r) => h("tr", { id: "inv-" + r.id, class: r.id === S.focusInvoice ? "sel" : "" },
      h("td", {}, h("span", { class: "badge " + (r.direction === "sales" ? "info" : "") }, r.direction)), h("td", {}, r.number || "–"), h("td", { class: "t-main" }, r.counterparty),
      h("td", { class: "num" }, r.issue_date || ""), h("td", { class: "num" }, r.due_date || ""), h("td", { class: "num t-amt" }, money(r.total, r.currency), r.currency !== cur() && r.total_base != null ? h("div", { class: "small muted" }, "≈ " + money(r.total_base, cur())) : null),
      h("td", {}, h("span", { class: "badge " + (r.status === "paid" ? "ok" : r.overdue ? "bad" : "warn") }, r.status === "paid" ? "paid" + (r.paid_date ? " " + r.paid_date : "") : r.overdue ? "overdue" : "unpaid")),
      h("td", { class: "row t-act" }, r.file_path ? h("a", { class: "btn sm", href: fileUrl(r.file_path), target: "_blank" }, "File") : null,
        h("button", { class: "btn sm", onclick: async () => { await api(`/api/invoices/${r.id}`, { method: "PATCH", body: { status: r.status === "paid" ? "unpaid" : "paid" } }); load(); } }, r.status === "paid" ? "Mark unpaid" : "Mark paid"),
        h("button", { class: "btn sm danger", onclick: async () => { if (confirm("Delete invoice?")) { await api(`/api/invoices/${r.id}`, { method: "DELETE" }); load(); } } }, "Delete")))))));
    if (S.focusInvoice) {  // opened from the email list: bring that invoice into view (highlighted)
      const row = document.getElementById("inv-" + S.focusInvoice);
      if (row) row.scrollIntoView({ behavior: "smooth", block: "center" }); else toast("That invoice no longer exists (deleted?)", true);
      S.focusInvoice = null;
    }
  }
  await load();
};

// ---------- cash book
const cashState = { label: "All time", start: "", end: "" };
R.cash = async (root) => {
  const cashName = S.accounts.find((a) => a.kind === "cash")?.name;
  root.append(h("h1", {}, cashName && cashName !== "Cash" ? `Cash (${cashName})` : "Cash"), h("p", { class: "sub" }, "Your cash book. Receipts paid in cash are booked here automatically. Record cash you take out at an ATM or receive in cash, and small cash expenses without a receipt."));
  const pick = h("div", { class: "card" }, periodPicker(cashState, () => go("cash")));
  const body = h("div", {}, working("Loading…"));
  root.append(pick, body);
  const d = await api(`/api/cash?start=${cashState.start || ""}&end=${cashState.end || ""}`);
  body.innerHTML = "";
  const base = cur();
  const bal = d.balances || {};
  const balText = Object.keys(bal).length ? Object.entries(bal).sort(([a], [b]) => (a === base ? -1 : b === base ? 1 : 0)).map(([c, v]) => money(v, c)).join(" · ") : money(0, base);
  body.append(h("div", { class: "kpis" },
    kpi("Cash on hand", balText, "all cash movements so far"),
    kpi("Cash in", money(d.cash_in || 0), cashState.label || "selected period"),
    kpi("Cash out", money(d.cash_out || 0), cashState.label || "selected period"),
    kpi("ATM withdrawals to record", d.withdrawals.length, "found in your bank statements")));

  // forms
  const cin = { date: today(), amount: "", description: "Hævning i automat", category: "Transfer between own accounts", currency: base };
  const cout = { date: today(), amount: "", description: "", counterparty: "", category: "Other expenses", currency: base };
  const inCats = [["Transfer between own accounts", "Cash taken out of the bank (ATM)"], ["Sales revenue", "Cash sale / payment from a customer"],
                  ["Owner deposit / withdrawal", "Own private money put in"], ["Other income", "Other cash income"]];
  const f = (label, obj, key, attrs = {}) => h("label", { class: "f" }, label, h("input", { value: obj[key], oninput: (e) => (obj[key] = e.target.value), ...attrs }));
  body.append(h("div", { class: "grid g2" },
    h("div", { class: "card" }, h("h2", {}, "Cash in"),
      h("div", { class: "form" }, f("Date", cin, "date", { type: "date" }), f("Amount", cin, "amount", { type: "number", step: "0.01", min: "0" }),
        h("label", { class: "f" }, "What is it", h("select", { onchange: (e) => (cin.category = e.target.value) }, inCats.map(([v, l]) => h("option", { value: v }, l)))),
        f("Text", cin, "description"), f("Currency", cin, "currency")),
      h("div", { class: "row end", style: "margin-top:10px" }, h("button", { class: "btn primary", onclick: async () => {
        if (!(Number(cin.amount) > 0)) return toast("Enter the amount", true);
        await api("/api/cash", { method: "POST", body: { ...cin, amount: Math.abs(Number(cin.amount)) } }); toast("Cash in recorded"); go("cash");
      } }, "Add cash in"))),
    h("div", { class: "card" }, h("h2", {}, "Cash expense without a receipt"),
      h("div", { class: "form" }, f("Date", cout, "date", { type: "date" }), f("Amount", cout, "amount", { type: "number", step: "0.01", min: "0" }),
        f("Paid to", cout, "counterparty"), f("Text", cout, "description"),
        h("label", { class: "f" }, "Category", catSelect(cout.category, (v) => (cout.category = v))), f("Currency", cout, "currency")),
      h("p", { class: "muted small", style: "margin-top:8px" }, "Have the receipt? Upload it in Receipts with payment = cash instead - it is booked here with the receipt attached."),
      h("div", { class: "row end" }, h("button", { class: "btn primary", onclick: async () => {
        if (!(Number(cout.amount) > 0)) return toast("Enter the amount", true);
        await api("/api/cash", { method: "POST", body: { ...cout, amount: -Math.abs(Number(cout.amount)), description: cout.description || cout.counterparty || "Cash expense" } });
        toast("Cash expense recorded"); go("cash");
      } }, "Add cash expense")))));

  if (d.withdrawals.length) body.append(h("div", { class: "card" }, h("h2", {}, "ATM withdrawals in your bank statements"),
    h("p", { class: "muted small" }, "Add them to the cash book so the cash balance is right. They are booked as a transfer (bank → cash), not as an expense."),
    table(["Date", "Account", "Text", "Amount", ""], d.withdrawals.map((t) => h("tr", {}, h("td", { class: "num" }, t.date), h("td", { class: "small muted" }, t.account || ""),
      h("td", { class: "t-main" }, t.description), h("td", { class: "t-amt" }, amt(t.amount, t.currency)),
      h("td", { class: "t-act" }, h("button", { class: "btn sm primary", onclick: async () => {
        await api("/api/cash", { method: "POST", body: { date: t.date, amount: Math.abs(t.amount), currency: t.currency, description: "Fra bank: " + t.description, category: "Transfer between own accounts", source_tx_id: t.id } });
        toast("Added to the cash book"); go("cash");
      } }, "Add to cash book")))))));

  const rows = d.rows || [];
  const openingRows = Object.entries(d.opening || {}).filter(([, v]) => v).map(([c, v]) => h("tr", {}, h("td", { class: "num" }, cashState.start), h("td", { class: "muted" }, "Balance carried forward"), h("td", {}), h("td", {}), h("td", {}), h("td", { class: "num" }, money(v, c)), h("td", {})));
  body.append(h("div", { class: "card" }, h("h2", {}, `Cash book (${rows.length})`),
    rows.length || openingRows.length ? table(["Date", "Text", "Category", "In", "Out", "Balance", ""], [...openingRows, ...rows.slice().reverse().map((t) => h("tr", {},
      h("td", { class: "num" }, t.date), h("td", { class: "t-main" }, t.counterparty || t.description, t.counterparty && t.description && !["Kontant: ", "Cash: "].some((p) => t.description === p + t.counterparty) ? h("div", { class: "desc" }, t.description) : null),
      h("td", { class: "small" }, t.category),
      h("td", { class: "num pos" }, t.amount > 0 ? money(t.amount, t.currency !== base ? t.currency : "") : ""),
      h("td", { class: "num" }, t.amount < 0 ? money(-t.amount, t.currency !== base ? t.currency : "") : ""),
      h("td", { class: "num" }, money(t.balance, t.currency !== base ? t.currency : "")),
      h("td", {}, t.receipt_id ? h("span", { class: "badge ok", title: "Receipt attached" }, "receipt")
        : h("span", {}, t.amount < 0 ? h("span", { class: "badge warn", title: "No receipt attached" }, "no receipt") : null, " ",
            h("button", { class: "btn sm ghost", title: "Delete", onclick: async () => { if (confirm("Delete this cash entry?")) { await api(`/api/transactions/${t.id}`, { method: "DELETE" }); go("cash"); } } }, "✕")))))])
    : h("p", { class: "empty" }, "No cash movements yet. Upload a cash receipt, or add cash in / out above.")));
};

// ---------- transactions
const txState = { q: "", start: "", end: "", category: "", account_id: "", only: "", offset: 0 };
const TX_PAGE = 200;
R.transactions = async (root) => {
  root.append(h("h1", {}, "Transactions"), h("p", { class: "sub" }, "Everything in the ledger. Change a category and it is remembered for that payee next time."));
  const remember = h("input", { type: "checkbox", checked: store.get("remember", true), onchange: (e) => store.set("remember", e.target.checked) });
  const q = h("input", { placeholder: "Search payee or text…", value: txState.q, "aria-label": "Search", oninput: debounce((e) => { txState.q = e.target.value; load(); }, 300) });
  const filters = h("div", { class: "filters" }, q,
    h("input", { type: "date", value: txState.start, "aria-label": "From date", onchange: (e) => { txState.start = e.target.value; load(); } }),
    h("input", { type: "date", value: txState.end, "aria-label": "To date", onchange: (e) => { txState.end = e.target.value; load(); } }),
    catSelect(txState.category, (v) => { txState.category = v; load(); }, { any: true }),
    h("select", { "aria-label": "Account", onchange: (e) => { txState.account_id = e.target.value; load(); } }, h("option", { value: "" }, "All accounts"), S.accounts.map((a) => h("option", { value: a.id, selected: String(a.id) === txState.account_id }, a.name))),
    h("select", { "aria-label": "Show", onchange: (e) => { txState.only = e.target.value; load(); } }, [["", "All transactions"], ["uncategorized", "Uncategorized only"], ["undocumented", "Money out without receipt"]].map(([v, l]) => h("option", { value: v, selected: v === txState.only }, l))));
  const info = h("div", { class: "row", style: "margin:12px 0" });
  const box = h("div"), more = h("div", { class: "row", style: "justify-content:center;margin-top:10px" });
  const manual = { date: today(), amount: "", description: "", counterparty: "", currency: cur(), category: "Uncategorized" };
  root.append(h("div", { class: "card" }, filters, info, box, more),
    h("details", { class: "card" }, h("summary", { style: "cursor:pointer;font-weight:600" }, "Add a transaction manually (goes into the cash book - see the Cash tab)"),
      h("div", { class: "form", style: "margin-top:10px" },
        h("label", { class: "f" }, "Date", h("input", { type: "date", value: manual.date, oninput: (e) => (manual.date = e.target.value) })),
        h("label", { class: "f" }, "Amount (negative = money out)", h("input", { type: "number", step: "0.01", oninput: (e) => (manual.amount = Number(e.target.value)) })),
        h("label", { class: "f" }, "Counterparty", h("input", { oninput: (e) => (manual.counterparty = e.target.value) })),
        h("label", { class: "f" }, "Description", h("input", { oninput: (e) => (manual.description = e.target.value) })),
        h("label", { class: "f" }, "Currency", h("input", { value: manual.currency, oninput: (e) => (manual.currency = e.target.value) })),
        h("label", { class: "f" }, "Category", catSelect(manual.category, (v) => (manual.category = v)))),
      h("div", { class: "row end", style: "margin-top:10px" }, h("button", { class: "btn primary", onclick: async () => {
        if (!manual.amount) return toast("Enter an amount", true);
        await api("/api/transactions", { method: "POST", body: { ...manual, account_name: "cash" } }); toast("Added to the cash book"); load();
      } }, "Add"))));

  const same = (a, b) => (a || "").trim().toLowerCase() === (b || "").trim().toLowerCase();
  const rowOf = (t) => {
    const title = t.counterparty || t.description || "–";
    const sub = t.counterparty && t.description && !same(t.counterparty, t.description) ? t.description : "";
    return h("tr", {},
      h("td", { class: "num" }, t.date),
      h("td", { class: "t-main" }, title, sub ? h("div", { class: "desc" }, sub) : null, t.notes ? h("div", { class: "desc" }, "📝 " + t.notes) : null),
      h("td", { class: "small muted" }, t.account || ""),
      h("td", { class: "t-amt" }, amt(t.amount, t.currency !== cur() ? t.currency : ""), fxLine(t.currency, t.amount_base, t.orig_amount, t.orig_currency, t)),
      h("td", {}, catSelect(t.category, async (v) => {
        const r = await api(`/api/transactions/${t.id}`, { method: "PATCH", body: { category: v, remember: remember.checked } });
        toast(r.rule ? `Saved - will remember “${r.rule}” → ${v}` : "Saved"); updateBadges();
      })),
      h("td", { class: "t-act" }, t.receipt_id ? h("span", { class: "badge ok", title: "Receipt attached" }, "receipt") : t.invoice_id ? h("span", { class: "badge ok" }, "invoice") : null,
        h("button", { class: "btn sm ghost", title: "Delete", "aria-label": "Delete transaction", onclick: async () => { if (confirm("Delete this transaction?")) { await api(`/api/transactions/${t.id}`, { method: "DELETE" }); load(); } } }, "✕")));
  };

  let shown = 0, tbody = null;
  async function load(append = false) {
    if (!append) txState.offset = 0;
    const p = new URLSearchParams(Object.entries({ ...txState, limit: TX_PAGE }).filter(([, v]) => v !== "" && v != null));
    const d = await api("/api/transactions?" + p);
    info.innerHTML = "";
    info.append(h("span", { class: "muted" }, `${d.total} transactions · net ${money(d.sum)}`), h("span", { class: "spacer" }),
      h("label", { class: "chk small", title: "When you change a category, save a rule so the same payee gets it automatically next time" }, remember, "Remember my category changes"),
      categorizeMenu(() => load()),
      h("button", { class: "btn", onclick: async () => { const r = await api("/api/export/transactions", { method: "POST", body: { start: txState.start, end: txState.end } }); location.href = r.url; } }, "Export CSV"));
    if (!append) {
      box.innerHTML = ""; shown = 0;
      if (!d.rows.length) { more.innerHTML = ""; return box.append(h("p", { class: "empty" }, "No transactions match.")); }
      const t = table(["Date", "Payee", "Account", "Amount", "Category", ""], d.rows.map(rowOf), "tx");
      tbody = t.querySelector("tbody"); box.append(t);
    } else {
      const t = table(["Date", "Payee", "Account", "Amount", "Category", ""], d.rows.map(rowOf));   // labels the cells
      tbody.append(...t.querySelectorAll("tbody > tr"));
    }
    shown += d.rows.length;
    more.innerHTML = "";
    if (d.total > shown) more.append(h("span", { class: "muted small" }, `Showing ${shown} of ${d.total}`),
      h("button", { class: "btn", onclick: () => { txState.offset = shown; load(true); } }, "Show more"));
  }
  await load();
};

// "Auto-categorize": what to look at, and whether the model is needed
function categorizeMenu(after) {
  const menu = h("details", { class: "menu" });
  const sum = h("summary", { class: "btn" }, "Auto-categorize ▾");
  const run = async (body) => {
    menu.open = false; sum.textContent = "Categorizing…"; sum.style.pointerEvents = "none";
    try {
      const r = await api("/api/transactions/recategorize", { method: "POST", body });
      const top = Object.entries(r.by_category || {}).slice(0, 3).map(([k, n]) => `${n} ${k}`).join(", ");
      toast(r.checked ? `Categorized ${r.categorized} of ${r.checked}` + (top ? ` (${top}${Object.keys(r.by_category).length > 3 ? ", …" : ""})` : "") + ` · ${r.still_uncategorized} still uncategorized` : "Nothing to categorize");
      (r.warnings || []).forEach((w) => toast(w, true));
    } catch (err) { toast(err.message, true); }
    sum.textContent = "Auto-categorize ▾"; sum.style.pointerEvents = ""; after();
  };
  const opt = (title, text, body) => h("button", { onclick: () => run(body) }, h("strong", {}, title), h("small", {}, text));
  const pop = h("div", { class: "pop" },
    opt("Uncategorized transactions", "Your rules and keywords first, then the model decides once per payee.", { scope: "uncategorized" }),
    opt("…and re-check the catch-alls", "Also lines the model earlier put in Other expenses / Other income. A category you chose yourself is never changed.", { scope: "weak" }),
    opt("Rules and keywords only", "No model needed - works while it is offline.", { scope: "weak", use_llm: false }));
  menu.append(sum, pop);
  api("/api/transactions/open-counts").then((n) => {
    pop.children[0].firstChild.textContent = `Uncategorized transactions (${n.uncategorized})`;
    pop.children[1].firstChild.textContent = `…and re-check the catch-alls (+${n.weak})`;
  }).catch(() => {});
  document.addEventListener("click", (e) => { if (menu.open && !menu.contains(e.target)) menu.open = false; });
  return menu;
}

function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

// ---------- reconcile
R.reconcile = async (root) => {
  root.append(h("h1", {}, "Reconcile"), h("p", { class: "sub" }, "Link receipts and invoices to bank transactions, and see which expenses still need a document."));
  const body = h("div", {}, working("Loading…"));
  root.append(h("div", { class: "row", style: "margin-bottom:14px" }, h("button", { class: "btn primary", onclick: async () => { const r = await api("/api/reconcile/auto", { method: "POST" }); toast(`${r.linked} new match(es)`); go("reconcile"); } }, "Auto-match now")), body);
  const d = await api("/api/reconcile");
  body.innerHTML = "";
  if (d.unknown_cards.length) body.append(h("div", { class: "card" }, h("h2", {}, `Cards on your receipts (${d.unknown_cards.length} not linked)`),
    h("p", { class: "muted small" }, "These card numbers (last 4 digits) appear on receipts but aren't linked to an account. Link each one so its receipts are matched within that account - or tell you which statement is missing."),
    table(["Card", "Receipts", "Account", ""], d.unknown_cards.map((k) => h("tr", {},
      h("td", {}, h("strong", {}, "••" + k.last4)), h("td", { class: "small" }, `${k.receipts} (${k.first} – ${k.last})`, k.suggested_reason ? h("div", { class: "muted" }, "Likely: " + k.suggested_reason) : null),
      h("td", { colspan: 2 }, linkCardControl(k.last4, k.suggested_account_id, () => go("reconcile")), " ",
        h("button", { class: "btn sm ghost", title: "This card isn't on any of my accounts - stop asking and don't match its receipts", onclick: async () => {
          await api("/api/cards/ignore", { method: "POST", body: { last4: k.last4 } }); toast(`Card ••${k.last4} ignored`); go("reconcile");
        } }, "Not my account")))))));
  body.append(h("div", { class: "card" }, h("h2", {}, `Suggested matches (${d.suggestions.length})`),
    d.suggestions.length ? table(["Document", "Bank transaction", "Score", ""], d.suggestions.map((sg) => h("tr", {},
      h("td", { class: "t-wide" }, h("span", { class: "badge" }, sg.doc_type), " ", sg.doc.merchant || sg.doc.counterparty, " · ", sg.doc.date || sg.doc.issue_date, " · ", money(sg.doc.total, sg.doc.currency)),
      h("td", { class: "t-wide" }, sg.transaction.date, " · ", amt(sg.transaction.amount), h("div", { class: "desc" }, sg.transaction.counterparty, " ", sg.transaction.description)),
      h("td", {}, h("span", { class: "badge " + (sg.score >= 0.75 ? "ok" : "warn") }, Math.round(sg.score * 100) + "%"), h("div", { class: "small muted" }, sg.reason)),
      h("td", { class: "t-act" }, h("button", { class: "btn sm primary", onclick: async () => { await api("/api/reconcile/link", { method: "POST", body: { doc_type: sg.doc_type, doc_id: sg.doc.id, transaction_id: sg.transaction.id } }); toast("Linked"); go("reconcile"); } }, "Link"))))) : h("p", { class: "empty" }, "No open suggestions.")));
  body.append(h("div", { class: "grid g2" },
    h("div", { class: "card" }, h("h2", {}, `Expenses without a document (${d.missing_count})`), h("p", { class: "muted small" }, `Total ${money(d.missing_total, cur())}. Transfers, taxes and bank fees are excluded.`),
      d.missing.length ? table(["Date", "Counterparty", "Amount"], d.missing.slice(0, 100).map((t) => h("tr", {}, h("td", { class: "num" }, t.date), h("td", { class: "t-main" }, t.counterparty || h("span", { class: "desc" }, t.description.slice(0, 60))), h("td", { class: "t-amt" }, amt(t.amount, t.currency !== cur() ? t.currency : ""), fxLine(t.currency, t.amount_base))))) : h("p", { class: "empty" }, "All expenses are documented.")),
    h("div", { class: "card" }, h("h2", {}, `Receipts without a bank transaction (${d.unmatched_receipts.length})`), h("p", { class: "muted small" }, "Cash receipts are booked in the cash book automatically. Link your cards to accounts to see which statement a receipt belongs to."),
      d.unmatched_receipts.length ? table(["Date", "Merchant", "Total", "Why"], d.unmatched_receipts.map((r) => h("tr", {}, h("td", { class: "num" }, r.date || "?"), h("td", { class: "t-main" }, r.merchant), h("td", { class: "num t-amt" }, money(r.total, r.currency)), h("td", { class: "small muted t-wide" }, r.why || "")))) : h("p", { class: "empty" }, "None."),
      h("h2", { style: "margin-top:18px" }, `Unpaid invoices (${d.open_invoices.length})`),
      d.open_invoices.length ? table(["Type", "Counterparty", "Due", "Total"], d.open_invoices.map((r) => h("tr", {}, h("td", {}, r.direction), h("td", {}, r.counterparty), h("td", { class: "num" }, r.due_date || ""), h("td", { class: "num" }, money(r.total, r.currency))))) : h("p", { class: "empty" }, "None."))));
};

// ---------- reports
const rpState = { label: "This year", start: periods()["This year"][0], end: periods()["This year"][1] };
R.reports = async (root) => {
  root.append(h("h1", {}, "Reports"), h("p", { class: "sub" }, "Excel overviews with live formulas and charts - open in Excel, LibreOffice or Google Sheets."));
  const title = h("input", { placeholder: "Title (optional)", style: "min-width:260px" });
  const result = h("div");
  const pick = h("div");
  const drawPick = () => { pick.innerHTML = ""; pick.append(periodPicker(rpState, drawPick)); };
  drawPick();
  root.append(h("div", { class: "card" }, h("h2", {}, "Standard overview workbook"),
    h("p", { class: "muted small" }, "Sheets: Summary · Monthly · Categories · Category × Month · Transactions · Receipts · Invoices · VAT · Missing documents"),
    pick, h("div", { class: "row", style: "margin-top:12px" }, title, h("span", { class: "spacer" }), h("button", { class: "btn primary", onclick: async (e) => {
      e.target.disabled = true; result.innerHTML = ""; result.append(working("Building workbook…"));
      try {
        const r = await api("/api/reports/overview", { method: "POST", body: { start: rpState.start, end: rpState.end, title: title.value } });
        const f = r.figures;
        result.innerHTML = "";
        result.append(h("div", { class: "notice ok" }, h("div", {}, h("a", { class: "file-link", href: r.url }, "⬇ ", r.filename),
          h("div", { class: "small", style: "margin-top:6px" }, `${r.start} – ${r.end}: income ${money(f.income, f.currency)}, expenses ${money(f.expenses, f.currency)}, net ${money(f.net, f.currency)} · ${f.transactions} transactions (${f.converted} converted from other currencies${f.without_rate ? `, ${f.without_rate} without rate` : ""}), ${f.receipts} receipts, ${f.missing_documents} expenses without document.`))));
        loadList();
      } catch (err) { result.innerHTML = ""; result.append(notices([err.message], "bad")); }
      e.target.disabled = false;
    } }, "Create Excel overview")), result));
  const examples = ["Spending per category per month this year as Excel with a chart", "Top 15 shops by spend this year, Excel with a bar chart",
    "All subscriptions and recurring payments with their monthly cost, as Excel", "Compare each spending category this quarter vs last quarter in Excel",
    "Export all transactions of last month to CSV"];
  root.append(h("div", { class: "card" }, h("h2", {}, "Custom overviews"), h("p", { class: "muted small" }, "Ask the Assistant in plain words; it writes the queries and builds the workbook."),
    h("div", { class: "chips" }, examples.map((x) => h("button", { class: "chip", onclick: () => { pendingPrompt = x; go("assistant"); } }, x)))));
  const listBox = h("div", { class: "card" });
  root.append(listBox);
  async function loadList() {
    const rows = await api("/api/reports");
    listBox.innerHTML = "";
    listBox.append(h("h2", {}, "Generated files"));
    if (!rows.length) return listBox.append(h("p", { class: "empty" }, "No reports yet."));
    listBox.append(table(["File", "Type", "Created", ""], rows.map((r) => h("tr", {}, h("td", {}, h("a", { href: r.url }, r.title || r.filename)), h("td", {}, h("span", { class: "badge" }, r.params.type || "")), h("td", { class: "small muted" }, r.created_at.replace("T", " ")),
      h("td", {}, h("button", { class: "btn sm ghost", onclick: async () => { await api(`/api/reports/${r.id}`, { method: "DELETE" }); loadList(); } }, "✕"))))));
  }
  await loadList();
};

// ---------- assistant
let pendingPrompt = null;
function md(text) {
  const lines = esc(text).split("\n");
  let out = "", inList = null, inCode = false, tbl = [];
  const inline = (s) => s.replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/(^|[^*])\*([^*]+)\*/g, "$1<i>$2</i>")
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, t, u) => /^(https?:\/\/|\/)/.test(u.replace(/&amp;/g, "&")) ? `<a href="${u}" target="_blank">${t}</a>` : m);
  const flushTable = () => {
    if (!tbl.length) return;
    const rows = tbl.filter((r) => !/^\s*\|?\s*:?-{2,}/.test(r)).map((r) => r.replace(/^\s*\||\|\s*$/g, "").split("|").map((c) => inline(c.trim())));
    out += "<div class='tablewrap'><table><thead><tr>" + rows[0].map((c) => `<th>${c}</th>`).join("") + "</tr></thead><tbody>" + rows.slice(1).map((r) => "<tr>" + r.map((c) => `<td>${c}</td>`).join("") + "</tr>").join("") + "</tbody></table></div>";
    tbl = [];
  };
  const closeList = () => { if (inList) { out += `</${inList}>`; inList = null; } };
  for (const ln of lines) {
    if (ln.startsWith("```")) { closeList(); flushTable(); out += inCode ? "</pre>" : "<pre>"; inCode = !inCode; continue; }
    if (inCode) { out += ln + "\n"; continue; }
    if (/^\s*\|.*\|\s*$/.test(ln)) { closeList(); tbl.push(ln); continue; }
    flushTable();
    let m;
    if ((m = ln.match(/^(#{1,4})\s+(.*)/))) { closeList(); out += `<h${Math.min(m[1].length + 1, 4)}>${inline(m[2])}</h${Math.min(m[1].length + 1, 4)}>`; }
    else if ((m = ln.match(/^\s*[-*]\s+(.*)/))) { if (inList !== "ul") { closeList(); out += "<ul>"; inList = "ul"; } out += `<li>${inline(m[1])}</li>`; }
    else if ((m = ln.match(/^\s*\d+[.)]\s+(.*)/))) { if (inList !== "ol") { closeList(); out += "<ol>"; inList = "ol"; } out += `<li>${inline(m[1])}</li>`; }
    else if (!ln.trim()) { closeList(); out += ""; }
    else { closeList(); out += `<p>${inline(ln)}</p>`; }
  }
  closeList(); flushTable(); if (inCode) out += "</pre>";
  return out;
}

R.assistant = async (root) => {
  root.append(h("h1", {}, "Assistant"), h("p", { class: "sub" }, "Ask about your money or ask for an overview. The model looks things up in the ledger, can categorize and reconcile, and builds Excel or CSV files."));
  const msgs = h("div", { class: "msgs" });
  const input = h("textarea", { placeholder: "e.g. How much did I spend on software this year, and what are the biggest subscriptions?", rows: 2 });
  const send = h("button", { class: "btn primary" }, "Send");
  const biz = (S.settings.books_type || "mixed") === "business";
  const examples = ["How did last month go? Income, spending and the biggest categories", "What are my subscriptions and what do they cost per month?",
    "Categorize my uncategorized transactions", "Where did my money go this year? Excel with a chart",
    biz ? "What VAT do I owe this quarter (estimate)?" : "Which purchases over 1,000 have no receipt?"];
  const chips = h("div", { class: "chips", style: "margin-bottom:8px" }, examples.map((x) => h("button", { class: "chip", onclick: () => { input.value = x; submit(); } }, x)),
    h("button", { class: "chip", onclick: () => { S.chat = []; store.set("chat", []); draw(); } }, "Clear chat"));
  root.append(h("div", { class: "card chat" }, msgs, chips, h("div", { class: "composer" }, input, send)));

  function draw() {
    msgs.innerHTML = "";
    if (!S.chat.length) msgs.append(h("div", { class: "empty" }, "Ask anything about your books."));
    for (const m of S.chat) {
      if (m.role === "user") msgs.append(h("div", { class: "msg user" }, m.content));
      else msgs.append(h("div", { class: "msg assistant" },
        m.steps && m.steps.length ? h("details", { class: "steps" }, h("summary", {}, `${m.steps.length} tool call(s)`), m.steps.map((x) => h("div", {}, "🔧 ", x))) : null,
        h("div", { html: md(m.content || "") }), (m.files || []).map((f) => h("a", { class: "file-link", href: f.url }, "⬇ " + f.filename))));
    }
    msgs.scrollTop = msgs.scrollHeight;
  }

  async function submit() {
    const text = input.value.trim();
    if (!text || send.disabled) return;
    input.value = ""; send.disabled = true;
    S.chat.push({ role: "user", content: text });
    const reply = { role: "assistant", content: "", steps: [], files: [] };
    draw();
    const live = h("div", { class: "msg assistant" }, h("div", { class: "steps" }), working("Thinking…"));
    msgs.append(live); msgs.scrollTop = msgs.scrollHeight;
    try {
      const r = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ messages: S.chat.map(({ role, content }) => ({ role, content })) }) });
      const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, i); buf = buf.slice(i + 1);
          if (!line.trim()) continue;
          const ev = JSON.parse(line);
          if (ev.type === "tool") { const a = JSON.stringify(ev.args); reply.steps.push(`${ev.name}(${a.length > 140 ? a.slice(0, 140) + "…" : a})`); live.firstChild.append(h("div", {}, "🔧 ", ev.name, "…")); live.lastChild.lastChild.textContent = `Running ${ev.name}…`; }
          else if (ev.type === "tool_result") { reply.steps[reply.steps.length - 1] += " → " + ev.summary; live.lastChild.lastChild.textContent = "Thinking…"; }
          else if (ev.type === "file") reply.files.push(ev);
          else if (ev.type === "answer") reply.content = ev.content;
          else if (ev.type === "error") reply.content = "⚠️ " + ev.content + (/Cannot reach|No model/.test(ev.content) ? "\n\nThe Assistant needs the model on your desktop. Everything else in the app keeps working without it." : "");
        }
      }
    } catch (e) { reply.content = "⚠️ " + e.message; }
    S.chat.push(reply); store.set("chat", S.chat.slice(-40));
    send.disabled = false; draw();
  }
  send.onclick = submit;
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } });
  draw();
  if (pendingPrompt) { input.value = pendingPrompt; pendingPrompt = null; submit(); }
};

// ---------- settings (one section at a time)
const SETTINGS_TABS = [["books", "Your books"], ["model", "Model"], ["accounts", "Accounts & cards"], ["categories", "Categories & rules"],
  ["email", "Email"], ["automation", "Automation"], ["prompts", "Prompts"], ["studio", "Studio"]];
const SET = {};

R.settings = async (root) => {
  root.append(h("h1", {}, "Settings"), h("p", { class: "sub" }, "Whose books these are, the model that reads for you, accounts, categories, email and the prompts."));
  let tab = store.get("settingsTab", "books");
  if (!SET[tab]) tab = "books";
  root.append(h("div", { class: "seg", role: "tablist" }, SETTINGS_TABS.map(([id, label]) =>
    h("button", { role: "tab", "aria-selected": String(id === tab), onclick: () => { store.set("settingsTab", id); go("settings"); } }, label))));
  await SET[tab](root);
};

// a small form bound to a copy of the settings
function settingsForm() {
  const st = { ...S.settings };
  return {
    st,
    inp: (label, key, attrs = {}) => h("label", { class: "f" }, label, h("input", { value: st[key] ?? "", oninput: (e) => (st[key] = e.target.value), ...attrs })),
    chk: (label, key) => h("label", { class: "chk" }, h("input", { type: "checkbox", checked: st[key] === "1", onchange: (e) => (st[key] = e.target.checked ? "1" : "0") }), label),
    save: async () => { S.settings = await api("/api/settings", { method: "PUT", body: st }); toast("Settings saved"); await refreshStatus(); },
  };
}

// ---------- shared form pieces: whose books, and which model (used by the first-run setup and by Settings)
function booksFields(st, packs) {
  const other = h("input", { value: st.country || "", placeholder: "Country", oninput: (e) => (st.country = e.target.value) });
  const cur_ = h("input", { value: st.base_currency || "", size: 5, maxlength: 3, oninput: (e) => (st.base_currency = e.target.value.toUpperCase()) });
  const sel = h("select", { onchange: (e) => {
    const p = packs.find((x) => x.id === e.target.value);
    st.country_pack = p.id; other.style.display = p.id === "generic" ? "" : "none";
    if (p.id !== "generic") { st.country = p.name; other.value = p.name; }
    if (!st._currency_touched) { st.base_currency = p.currency; cur_.value = p.currency; }
  } }, packs.map((p) => h("option", { value: p.id, selected: p.id === st.country_pack }, p.id === "generic" ? "Another country" : p.name)));
  other.style.display = st.country_pack === "generic" ? "" : "none";
  cur_.addEventListener("input", () => (st._currency_touched = true));
  const vat = h("label", { class: "chk" }, h("input", { type: "checkbox", checked: st.vat_registered === "1", onchange: (e) => (st.vat_registered = e.target.checked ? "1" : "0") }), "VAT registered");
  const showVat = () => { vat.style.display = (st.books_type || "private") === "private" ? "none" : ""; };
  showVat();
  return h("div", {},
    h("div", { class: "form" },
      h("label", { class: "f" }, "These books are", h("select", { onchange: (e) => { st.books_type = e.target.value; showVat(); } },
        [["private", "Private finances"], ["mixed", "Private + some business"], ["business", "A business"]]
          .map(([v, l]) => h("option", { value: v, selected: (st.books_type || "private") === v }, l)))),
      h("label", { class: "f" }, "Your name (person or company)", h("input", { value: st.company_name || "", placeholder: "as on invoices addressed to you", oninput: (e) => (st.company_name = e.target.value) })),
      h("label", { class: "f" }, "Your name on bank lines", h("input", { value: st.owner_names || "", placeholder: "e.g. ANNA SMITH, Anna M. Smith", oninput: (e) => (st.owner_names = e.target.value) })),
      h("label", { class: "f" }, "Country", sel, other),
      h("label", { class: "f" }, "Base currency (ISO code)", cur_)),
    h("p", { class: "muted small", style: "margin-top:8px" }, "The model is told this with every document. “Your name on bank lines” (comma separated): payments to or from these names are your own money moving between accounts, so they are booked as transfers. The country adds what receipts and bank lines are called there",
      " (", packs.filter((p) => p.has_skill).map((p) => p.name).join(", "), " have their own notes; other countries work with the general rules)."),
    h("div", { style: "margin-top:6px" }, vat));
}

// provider preset, address, key, model, "Test" with what the server can do
function modelFields(st, presets, opts = {}) {
  const out = h("div");
  const note = h("p", { class: "muted small", style: "margin:8px 0 0" });
  const url = h("input", { value: st.llm_base_url || "", placeholder: "http://…/v1", oninput: (e) => (st.llm_base_url = e.target.value.trim()) });
  const key = h("input", { type: "password", autocomplete: "off", placeholder: opts.keySaved ? "saved - type a new key to replace" : "none", oninput: (e) => (st.llm_api_key = e.target.value.trim()) });
  const model = h("input", { value: st.llm_model || "", list: "model-list", placeholder: "automatic", oninput: (e) => (st.llm_model = e.target.value.trim()) });
  const list = h("datalist", { id: "model-list" });
  const guess = () => presets.find((p) => p.id === st.llm_provider) || presets.find((p) => p.url && p.url === st.llm_base_url) || presets[presets.length - 1];
  const showNote = (p) => {
    note.textContent = p.note + (p.kind === "local" && opts.docker ? " In Docker, a server on the same computer is reached at host.docker.internal." : "");
    note.className = "small " + (p.kind === "cloud" ? "" : "muted");
    note.style.color = p.kind === "cloud" ? "var(--warn-ink)" : "";
  };
  const prov = h("select", { onchange: (e) => {
    const p = presets.find((x) => x.id === e.target.value);
    st.llm_provider = p.id;
    if (p.url) { st.llm_base_url = p.url; url.value = p.url; }
    st.llm_model = ""; model.value = ""; list.innerHTML = ""; out.innerHTML = "";
    showNote(p);
  } }, presets.map((p) => h("option", { value: p.id, selected: p.id === guess().id }, p.label + (p.kind === "cloud" ? " (cloud)" : ""))));
  showNote(guess());
  const test = h("button", { class: "btn", type: "button", onclick: async () => {
    if (!st.llm_base_url) return toast("Enter the server address first", true);
    test.disabled = true; out.innerHTML = ""; out.append(working("Asking the model server… (a few short test requests)"));
    let r;
    try { r = await api("/api/llm/test", { method: "POST", body: { base_url: st.llm_base_url, api_key: st.llm_api_key || "", model: st.llm_model || "" } }); }
    catch (e) { r = { reachable: false, error: e.message }; }
    test.disabled = false; out.innerHTML = "";
    list.innerHTML = ""; (r.models || []).forEach((m) => list.append(h("option", { value: m })));
    const line = (ok, text) => h("li", {}, h("span", { class: "badge " + (ok ? "ok" : "bad") }, ok ? "ok" : "no"), " ", text);
    const items = [line(r.reachable, r.reachable ? `Server answers - ${r.models.length} model(s)` + (r.using ? `, using ${r.using}` : "") : r.error || "No answer")];
    if (r.reachable && !r.using) {
      items.push(h("li", {}, "Choose a model: ", h("span", { class: "chips", style: "display:inline-flex" }, (r.models || []).slice(0, 12).map((m) =>
        h("button", { class: "chip", type: "button", onclick: () => { st.llm_model = m; model.value = m; test.click(); } }, m))), r.models.length > 12 ? " … or type its name in the Model field." : ""));
    }
    if (r.json) items.push(line(r.json.ok, "Structured answers: " + r.json.detail));
    if (r.vision) items.push(line(r.vision.ok, "Photos and scans: " + r.vision.detail + (r.vision.ok ? "" : " - PDFs with text and emails still work")));
    if (r.tools) items.push(line(r.tools.ok, "Assistant tools: " + r.tools.detail));
    out.append(h("div", { class: "notice " + (r.reachable && r.json?.ok ? "ok" : "warn") }, h("ul", { style: "list-style:none;padding:0;display:grid;gap:5px" }, items)));
    if (r.vision && !r.vision.ok && st.llm_vision !== "no") out.append(h("p", { class: "small muted" }, "With a text-only model, set “Reads images” to No and use the OCR server for photos - or pick a vision model."));
    opts.onTest && opts.onTest(r);
  } }, "Test");
  return h("div", {},
    h("div", { class: "form" },
      h("label", { class: "f" }, "Where the model runs", prov),
      h("label", { class: "f" }, "Address (ends in /v1)", url),
      h("label", { class: "f" }, "API key", key),
      h("label", { class: "f" }, "Model", model, list),
      h("label", { class: "f" }, "Reads images", h("select", { onchange: (e) => (st.llm_vision = e.target.value) },
        [["auto", "Find out (recommended)"], ["yes", "Yes - a vision model"], ["no", "No - text only"]].map(([v, l]) => h("option", { value: v, selected: (st.llm_vision || "auto") === v }, l))))),
    note, h("div", { class: "row", style: "margin-top:10px" }, test, opts.extra || null), out);
}

// ---------- first-run setup (shown once, while the ledger is empty)
R.setup = async (root) => {
  const info = await api("/api/setup");
  const st = { ...S.settings, country_pack: info.pack, llm_api_key: "" };
  const preset = info.presets.find((p) => p.url === st.llm_base_url);
  st.llm_provider = st.llm_provider || (preset ? preset.id : "");
  const finish = h("button", { class: "btn primary", onclick: async () => {
    finish.disabled = true;
    try {
      const body = { ...st }; delete body._currency_touched;
      await api("/api/setup", { method: "POST", body });
      await refreshMeta(); refreshStatus(); toast("Ready - import a bank statement or add receipts to begin"); go("overview");
    } catch (e) { toast(e.message, true); finish.disabled = false; }
  } }, "Save and start");
  root.append(h("h1", {}, "Welcome to your ledger"),
    h("p", { class: "sub" }, "Two things to set up: whose books these are, and which model reads your documents. Everything can be changed later under Settings."),
    h("div", { class: "card" }, h("h2", {}, "1 · Whose books are these?"), booksFields(st, info.packs)),
    h("div", { class: "card" }, h("h2", {}, "2 · The model that reads for you"),
      h("p", { class: "muted small" }, "The ledger talks to any OpenAI-compatible model server. A model on your own computer keeps everything private; a cloud API works without a GPU but your receipts and bank lines are sent to that provider. For photos of receipts the model must be able to read images."),
      modelFields(st, info.presets, { docker: info.docker })),
    h("div", { class: "row" }, finish, h("button", { class: "btn ghost", onclick: async () => { await api("/api/setup/skip", { method: "POST" }); go("overview"); } }, "Skip for now"),
      h("span", { class: "muted small" }, "Without a model you can still browse, categorize by keywords and build reports; reading statements and receipts needs one.")));
};

SET.books = async (root) => {
  const info = await api("/api/setup");
  const st = { ...S.settings, country_pack: info.pack };
  const inp = (label, key, attrs = {}) => h("label", { class: "f" }, label, h("input", { value: st[key] ?? "", oninput: (e) => (st[key] = e.target.value), ...attrs }));
  const save = async () => { const body = { ...st, llm_api_key: "" }; delete body._currency_touched; S.settings = await api("/api/settings", { method: "PUT", body }); toast("Settings saved"); };
  root.append(h("div", { class: "card" }, h("h2", {}, "Whose books are these?"), booksFields(st, info.packs),
    h("h3", {}, "Matching"),
    h("div", { class: "form" }, inp("Receipt ↔ bank match window (days)", "receipt_match_days", { type: "number" }), inp("Ignore missing documents below (amount)", "missing_doc_threshold", { type: "number" })),
    h("div", { class: "row", style: "margin-top:14px" }, h("button", { class: "btn primary", onclick: save }, "Save"),
      info.empty ? h("button", { class: "btn", title: "Replace the starter categories with the ones for the chosen country (only while the ledger is empty)", onclick: async () => {
        const body = { ...st, llm_api_key: "" }; delete body._currency_touched; const r = await api("/api/setup", { method: "POST", body });
        await refreshMeta(); toast(r.categories_from_pack ? "Saved - starter categories now follow the country" : "Saved");
      } }, "Save and reset starter categories") : null)));
  const fxBox = h("div", {}, working("Checking exchange rates…"));
  const conv = { amount: 100, frm: "EUR", to: cur(), on: today() };
  const convOut = h("strong", { class: "num" });
  const doConvert = async () => {
    try { const r = await api(`/api/fx/convert?amount=${conv.amount}&frm=${conv.frm}&to=${conv.to}&on=${conv.on}`); convOut.textContent = `= ${money(r.converted, r.to)}  (rate ${r.rate}, ${r.rate_date})`; }
    catch (e) { convOut.textContent = e.message; }
  };
  root.append(h("div", { class: "card" }, h("h2", {}, "Currencies & exchange rates"),
    h("p", { class: "muted small" }, `Everything is reported in ${cur()}. Amounts in other currencies are converted with the ECB euro reference rate of the transaction date (last business day before weekends/holidays). Rates are downloaded once and cached; a rate you enter yourself on a transaction or receipt always wins.`),
    fxBox,
    h("h3", {}, "Currency converter"),
    h("div", { class: "row" }, h("input", { type: "number", step: "0.01", value: conv.amount, style: "width:110px", oninput: (e) => (conv.amount = e.target.value) }),
      h("input", { value: conv.frm, size: 4, oninput: (e) => (conv.frm = e.target.value.toUpperCase()) }), "→",
      h("input", { value: conv.to, size: 4, oninput: (e) => (conv.to = e.target.value.toUpperCase()) }),
      h("input", { type: "date", value: conv.on, oninput: (e) => (conv.on = e.target.value) }),
      h("button", { class: "btn", onclick: doConvert }, "Convert"), convOut)));
  (async () => {
    const st = await api("/api/fx/status");
    fxBox.innerHTML = "";
    fxBox.append(st.ok ? h("div", { class: "row" }, h("span", { class: "badge ok" }, "ECB rates loaded"), h("span", { class: "small muted" }, `latest ${st.last_date} · ${st.currencies.length} currencies · downloaded ${st.downloaded}`),
      st.missing ? h("span", { class: "badge warn" }, `${st.missing} transaction(s) without rate`) : null, h("span", { class: "spacer" }),
      h("button", { class: "btn sm", onclick: async (e) => { e.target.disabled = true; try { const r = await api("/api/fx/refresh", { method: "POST" }); toast(`Rates updated to ${r.status.last_date}`); go("settings"); } catch (err) { toast(err.message, true); e.target.disabled = false; } } }, "Update rates now"))
      : notices([st.error], "bad"));
  })();


  // who can reach the books
  const acc = await api("/api/access");
  root.append(h("div", { class: "card" }, h("h2", {}, "Access"),
    acc.password ? h("p", { class: "small" }, h("span", { class: "badge ok" }, "password on"), " Browsers sign in once; scripts and phone shortcuts send the password as “Authorization: Bearer …”. ",
        h("button", { class: "btn sm", onclick: async () => { await api("/api/logout", { method: "POST" }); location.href = "/login"; } }, "Sign out"))
      : h("p", { class: "small" }, h("span", { class: "badge " + (acc.from === "the internet" ? "bad" : "") }, "no password"),
        ` You are connected from ${acc.from}. Anyone who can reach this address can read and change the books - fine on your own computer or a private network such as a VPN. `,
        "To require a password, start the app with the environment variable LEDGER_PASSWORD set.")));
};

SET.model = async (root) => {
  const { st, chk, save } = settingsForm();
  st.llm_api_key = "";
  const [presets, acc] = await Promise.all([api("/api/llm/presets"), api("/api/access")]);
  const inp = (label, key, attrs = {}) => h("label", { class: "f" }, label, h("input", { value: st[key] ?? "", oninput: (e) => (st[key] = e.target.value), ...attrs }));
  const ocrOut = h("div");
  const saveModel = async () => { await save(); await api("/api/status?fresh=true"); await refreshStatus(); };
  root.append(h("div", { class: "grid g2" },
    h("div", { class: "card" }, h("h2", {}, "Model"),
      modelFields(st, presets, { keySaved: S.settings.llm_api_key_set, docker: acc.docker }),
      h("h3", {}, "How it is used"),
      h("div", { class: "form" }, inp("Second address (same server, e.g. on the LAN)", "llm_fallback_url", { placeholder: "optional" }),
        h("label", { class: "f" }, "Reasoning when reading documents",
          h("select", { onchange: (e) => (st.llm_doc_reasoning = e.target.value) },
            [["off", "Off - fast; re-reads carefully only if checks fail (recommended)"], ["low", "Low"], ["medium", "Medium"], ["xhigh", "Maximum - slowest"]]
              .map(([v, l]) => h("option", { value: v, selected: (st.llm_doc_reasoning || "off") === v }, l))))),
      h("p", { class: "muted small", style: "margin-top:8px" }, "Leave Model on automatic only when the server has exactly one model. Naming the model makes sure a small test model that happens to be loaded never reads your documents."),
      h("div", { class: "stack", style: "margin-top:6px" }, chk("Let the model reason in the Assistant", "llm_thinking_chat")),
      h("div", { class: "row", style: "margin-top:12px" }, h("button", { class: "btn primary", onclick: saveModel }, "Save"))),
    h("div", { class: "card" },
      h("h2", {}, "OCR model (optional)"),
      h("p", { class: "muted small" }, "A small dedicated OCR model transcribes photos and scans first (optional; start it with run_ocr.sh on a machine with a GPU). The main model then reads the image and the transcription, and receipts whose total or VAT the OCR did not see are flagged. Digital PDFs use their own text and skip OCR."),
      h("div", { class: "form" }, inp("OCR server URL", "ocr_url"),
        h("label", { class: "f" }, "Mode", h("select", { onchange: (e) => (st.ocr_mode = e.target.value) },
          [["both", "Image + OCR text (most accurate)"], ["text", "OCR text only (fastest)"], ["off", "Off - main model only"]].map(([v, l]) => h("option", { value: v, selected: st.ocr_mode === v }, l))))),
      h("div", { class: "row", style: "margin-top:8px" }, h("button", { class: "btn", onclick: async () => {
        await saveModel(); const o = S.status.ocr || {}; ocrOut.innerHTML = ""; ocrOut.append(notices([o.mode === "off" ? "OCR is off." : o.ok ? `OCR server running (${o.model}).` : (o.error || "OCR server not reachable")], o.ok || o.mode === "off" ? "ok" : "bad"));
      } }, "Save and test OCR")), ocrOut)));
};

SET.accounts = async (root) => {
  const acc = { name: "", iban: "", currency: cur() };
  root.append(h("div", { class: "card" }, h("h2", {}, "Accounts"),
    h("p", { class: "muted small" }, "Cards: the last 4 digits of each payment card on the account, so receipts that show the card are matched to the right account. Add Apple Pay / Google Pay too - your phone and watch show their own last 4 digits on receipts."),
    S.accounts.length ? table(["Name", "IBAN / number", "Currency", "Transactions", "Cards (last 4 digits)", ""], S.accounts.map((a) => h("tr", {},
      h("td", {}, h("input", { value: a.name, onchange: async (e) => { await api(`/api/accounts/${a.id}`, { method: "PATCH", body: { name: e.target.value } }); await refreshMeta(); toast("Saved"); } })),
      h("td", {}, h("input", { value: a.iban || "", onchange: async (e) => { await api(`/api/accounts/${a.id}`, { method: "PATCH", body: { iban: e.target.value } }); toast("Saved"); } })),
      h("td", {}, a.currency), h("td", { class: "num" }, a.n), h("td", {}, a.kind === "cash" ? h("span", { class: "muted small" }, "cash") : cardCell(a)),
      h("td", {}, a.kind === "cash" ? null : h("button", { class: "btn sm danger", onclick: async () => {
        const msg = a.n ? `Delete the account "${a.name}" with its ${a.n} transactions, statements and linked cards?\n\nReceipts and invoices matched to those transactions are kept, but unlinked. This can't be undone.`
                        : `Delete the empty account "${a.name}"?`;
        if (!confirm(msg)) return;
        const r = await api(`/api/accounts/${a.id}`, { method: "DELETE" }); toast(`Account deleted (${r.deleted_transactions} transactions)`); await refreshMeta(); go("settings");
      } }, "Delete"))))) : h("p", { class: "muted" }, "Accounts are created when you import a statement, or add one here."),
    h("div", { class: "row", style: "margin-top:10px" }, h("input", { placeholder: "Account name", oninput: (e) => (acc.name = e.target.value) }), h("input", { placeholder: "IBAN", oninput: (e) => (acc.iban = e.target.value) }),
      h("input", { placeholder: "Currency", value: acc.currency, size: 5, oninput: (e) => (acc.currency = e.target.value.toUpperCase()) }),
      h("button", { class: "btn", onclick: async () => { if (!acc.name) return; await api("/api/accounts", { method: "POST", body: acc }); await refreshMeta(); go("settings"); } }, "Add account")),
    ignoredCardsLine()));

};

SET.categories = async (root) => {
  const nc = { name: "", kind: "expense", description: "", keywords: "" };
  const kinds = ["income", "expense", "transfer", "other"];
  root.append(h("div", { class: "card" }, h("h2", {}, "Categories"), h("p", { class: "muted small" }, "Keywords (comma separated) categorize matching transactions instantly, before the model is asked. The description helps the model choose."),
    table(["Name", "Kind", "Description", "Keywords", ""], S.categories.map((c) => {
      const patch = async (body) => { S.categories = await api(`/api/categories/${c.id}`, { method: "PATCH", body }); toast("Saved"); };
      return h("tr", {}, h("td", {}, h("input", { value: c.name, onchange: (e) => patch({ name: e.target.value }) })),
        h("td", {}, h("select", { onchange: (e) => patch({ kind: e.target.value }) }, kinds.map((k) => h("option", { selected: k === c.kind }, k)))),
        h("td", {}, h("input", { value: c.description, style: "width:100%", onchange: (e) => patch({ description: e.target.value }) })),
        h("td", {}, h("input", { value: c.keywords, style: "width:100%", onchange: (e) => patch({ keywords: e.target.value }) })),
        h("td", {}, c.name === "Uncategorized" ? null : h("button", { class: "btn sm ghost", title: "Delete", onclick: async () => { if (confirm(`Delete “${c.name}”? Its transactions become Uncategorized.`)) { S.categories = await api(`/api/categories/${c.id}`, { method: "DELETE" }); go("settings"); } } }, "✕")));
    })),
    h("div", { class: "row", style: "margin-top:10px" }, h("input", { placeholder: "New category", oninput: (e) => (nc.name = e.target.value) }),
      h("select", { onchange: (e) => (nc.kind = e.target.value) }, kinds.map((k) => h("option", { selected: k === "expense" }, k))),
      h("input", { placeholder: "Description", oninput: (e) => (nc.description = e.target.value) }), h("input", { placeholder: "Keywords", oninput: (e) => (nc.keywords = e.target.value) }),
      h("button", { class: "btn", onclick: async () => { if (!nc.name) return; S.categories = await api("/api/categories", { method: "POST", body: nc }); go("settings"); } }, "Add category"))));

  const rules = await api("/api/rules");
  root.append(h("div", { class: "card" }, h("h2", {}, `Learned rules (${rules.length})`), h("p", { class: "muted small" }, "Created when you correct a category with ‘remember’ ticked. If the text appears in a transaction, it gets this category."),
    rules.length ? table(["If text contains", "Category", "Created", ""], rules.map((r) => h("tr", {}, h("td", { class: "mono" }, r.pattern), h("td", {}, r.category), h("td", { class: "small muted" }, (r.created_at || "").slice(0, 10)),
      h("td", {}, h("button", { class: "btn sm ghost", onclick: async () => { await api(`/api/rules/${r.id}`, { method: "DELETE" }); go("settings"); } }, "✕"))))) : h("p", { class: "empty" }, "No rules yet.")));

};

SET.email = async (root) => { root.append(await emailSettingsCard()); };
SET.automation = async (root) => { if (!S.status) await refreshStatus(); root.append(desktopCard()); };

SET.prompts = async (root) => {
  const skills = await api("/api/skills");
  const ed = h("textarea", { rows: 24, spellcheck: "false" });
  const sel = h("select", { "aria-label": "Skill" }, skills.map((s) => h("option", { value: s.folder, selected: s.folder === store.get("skillSel", "") }, s.name + (s.edited ? "  (edited)" : ""))));
  const resetBtn = h("button", { class: "btn", title: "Throw away your changes to this skill and use the built-in text again", onclick: async () => {
    if (!confirm("Go back to the built-in text of this skill? Your edited version is deleted.")) return;
    await api(`/api/skills/${sel.value}`, { method: "DELETE" }); toast("Back to the built-in text"); store.set("skillSel", sel.value); go("settings");
  } }, "Reset to built-in");
  const desc = h("p", { class: "muted small" });
  const loadSkill = async () => {
    const r = await api(`/api/skills/${sel.value}`); ed.value = r.text; const sk = skills.find((s) => s.folder === sel.value) || {};
    desc.textContent = sk.description || ""; resetBtn.style.display = sk.edited && sk.builtin ? "" : "none";
  };
  sel.onchange = loadSkill;
  const prev = h("pre", { class: "mono", style: "white-space:pre-wrap;max-height:520px;overflow:auto;background:var(--surface-2);padding:10px;border-radius:8px;display:none" });
  const size = h("span", { class: "small muted" });
  const pv = h("select", { "aria-label": "Prompt to preview" }, ["assistant", "receipts", "invoices", "statements", "statement columns", "categorize", "email sorting"].map((t) => h("option", {}, t)));
  root.append(h("div", { class: "card" }, h("h2", {}, "What the model is told"),
    h("p", { class: "muted small" }, "Every request starts with bookkeeper-core, then the skill for the job, then your books (type, owner, accounts, categories) and the task. Preview shows the exact text."),
    h("div", { class: "row" }, pv, h("button", { class: "btn", onclick: async () => {
      const r = await api(`/api/prompt-preview?tab=${encodeURIComponent(pv.value)}`); prev.textContent = r.prompt; prev.style.display = "block";
      size.textContent = `${r.chars.toLocaleString()} characters ≈ ${r.tokens_estimate.toLocaleString()} tokens${r.note ? " (" + r.note + ")" : ""}`;
    } }, "Preview full prompt"), size), prev),
    h("div", { class: "card" }, h("h2", {}, "Skills"),
      h("p", { class: "muted small" }, "Each skill is a SKILL.md file. A section whose heading ends with “(chat)” is only used in the Assistant; one ending with “(reading)” only when the app reads documents or categorizes. The country-… skills hold the local vocabulary and are added to the others automatically. Your changes are saved as your own copy (updates of the app never overwrite them) and apply to the next request."),
      h("div", { class: "row" }, sel), desc, ed,
      h("div", { class: "row end", style: "margin-top:8px" }, resetBtn, h("button", { class: "btn primary", onclick: async () => { await api(`/api/skills/${sel.value}`, { method: "PUT", body: { text: ed.value } }); toast("Skill saved"); store.set("skillSel", sel.value); go("settings"); } }, "Save skill"))));
  await loadSkill();
};

SET.studio = async (root) => {
  const out = h("div");
  root.append(h("div", { class: "card" }, h("h2", {}, "Use the ledger from another chat app"),
    h("p", { class: "muted small" }, "This app is also an MCP tool server (address: " + location.origin + "/mcp), so any chat app that speaks MCP can read and update the same ledger. Unsloth Studio can be set up with one click:"),
    h("h3", {}, "1 · Tools"),
    h("p", { class: "muted small" }, "Registers this app's tool server (MCP) in Studio. Studio must be running."),
    h("div", { class: "row" }, h("button", { class: "btn", onclick: async () => { try { const r = await api("/api/studio/register-mcp", { method: "POST" }); out.innerHTML = ""; out.append(notices([r.message || `Registered ${r.url} in Studio. Enable the 'Bookkeeping Ledger' tools in a chat.`], "ok")); } catch (e) { out.innerHTML = ""; out.append(notices([e.message], "bad")); } } }, "Register tools in Studio")),
    h("h3", {}, "2 · Skills"),
    h("p", { class: "muted small" }, "Studio reads skills from ~/.agents/skills on the computer it runs on. When Studio runs on another computer than this app, download the skills and unpack them there; the zip also holds the project instructions to paste into a Studio project."),
    h("div", { class: "row" }, h("a", { class: "btn", href: "/api/skills.zip" }, "Download skills (.zip)"),
      h("button", { class: "btn", onclick: async () => { try {
        const r = await api("/api/studio/install-skills", { method: "POST" }); out.innerHTML = "";
        out.append(notices([r.same_machine ? `Installed ${r.installed.length} skills into ${r.folder}. Enable them in Studio > Skills.`
          : `Copied ${r.installed.length} skills to ${r.folder} on this server - but Studio runs on another computer, so use the download instead.`], r.same_machine ? "ok" : "warn"));
      } catch (e) { toast(e.message, true); } } }, "Install on this computer")),
    out));
};

// ---------------------------------------------------------------- router
async function go(tab) {
  if (location.hash !== "#" + tab) { history.replaceState(null, "", "#" + tab); }
  for (const b of document.querySelectorAll("[data-tab]")) b.setAttribute("aria-selected", b.dataset.tab === tab);
  $("#more-btn")?.setAttribute("aria-selected", !BOTTOM.includes(tab));
  const root = view();
  root.innerHTML = "";
  hideTip();
  window.scrollTo(0, 0);
  try { await R[tab](root); } catch (e) { root.append(notices([e.message], "bad")); console.error(e); }
  updateBadges();
}

(async function init() {
  drawNav();
  $("#llm-pill").onclick = () => { store.set("settingsTab", "model"); go("settings"); };
  const hideBtn = $("#hide-btn");
  const drawHide = () => {
    hideBtn.textContent = hideAmounts ? "Show amounts" : "Hide amounts";
    hideBtn.setAttribute("aria-pressed", hideAmounts);
    document.body.classList.toggle("amounts-hidden", hideAmounts);
  };
  hideBtn.onclick = () => {
    hideAmounts = !hideAmounts; store.set("hideAmounts", hideAmounts); drawHide();
    const t = location.hash.slice(1); go(R[t] ? t : "overview");
  };
  drawHide();
  await refreshMeta();
  let setupNeeded = false;
  try { setupNeeded = (await api("/api/setup")).needed; } catch {}
  refreshStatus();  // don't block the page on the model server
  setInterval(refreshStatus, 30000);
  const start = location.hash.slice(1);
  go(setupNeeded ? "setup" : R[start] && start !== "setup" ? start : "overview");
  window.addEventListener("hashchange", () => { const t = location.hash.slice(1); if (R[t]) go(t); });
})();
