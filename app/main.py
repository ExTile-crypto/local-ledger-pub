"""Web server: REST API for the tabs, file serving, streaming assistant, MCP endpoint (tools for other chat apps)."""
import asyncio
import json
import os
import re
import shutil
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

import httpx
from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import reports, skills, tools
from .assistant import run_chat
from .categorize import categorize_rows, learn_rule
from .config import BASE_DIR, DATA_DIR, REPORT_DIR, STATIC_DIR, UPLOAD_DIR
from .db import categories, db, get_settings, init_db, loads, now, set_settings
from .documents import extract_document
from . import cards, cash, desktop, fx, mail, packs, store, upload_queue
from .ledger import base_currency, doc_fx, recompute, tx_fx
from .importers import add_fingerprints, parse_statement
from .llm import LLM, LLMError, LLMOffline
from mcp.server.transport_security import TransportSecuritySettings

from .mcp_server import mcp
from . import ocr
from .reconcile import auto_match, link, missing_documents, suggestions

init_db()
# The MCP endpoint only answers requests addressed to these hosts (protection against DNS rebinding).
# Add the names and addresses the app is reached under with LEDGER_HOSTS, e.g. "192.168.1.20,ledger.example.net".
_hosts = ["127.0.0.1", "localhost", "[::1]"] + [h.strip() for h in os.environ.get("LEDGER_HOSTS", "").split(",") if h.strip()]
mcp_app = mcp.streamable_http_app(
    streamable_http_path="/mcp", stateless_http=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=[f"{h}:*" for h in _hosts] + _hosts,
        allowed_origins=[f"{proto}://{h}:*" for h in _hosts for proto in ("http", "https")]))


@asynccontextmanager
async def lifespan(app):
    asyncio.get_running_loop().run_in_executor(None, recompute)  # fill any missing base-currency amounts
    scan_task = asyncio.create_task(mail.scheduler())           # email inboxes, every email_scan_hours
    queue_task = asyncio.create_task(upload_queue.worker())     # receipts uploaded while the model was offline
    desktop_task = asyncio.create_task(desktop.scheduler())     # wake the GPU desktop, do the model work, power it off
    async with mcp.session_manager.run():
        yield
    scan_task.cancel()
    queue_task.cancel()
    desktop_task.cancel()


app = FastAPI(title="Local Ledger", lifespan=lifespan)


@app.middleware("http")
async def no_stale_ui(request, call_next):
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"  # always revalidate, so UI updates show up immediately
    return resp


# ------------------------------------------------------------------ optional password (LEDGER_PASSWORD)
# The app has no user accounts: it is meant for one person on a private network (your own machine, a VPN such as
# Tailscale). Set LEDGER_PASSWORD when it can be reached from anywhere else. Browsers sign in once and get a cookie;
# scripts, phone shortcuts and MCP clients send "Authorization: Bearer <password>".
import hashlib  # noqa: E402
import hmac  # noqa: E402
import secrets  # noqa: E402
import time as _time  # noqa: E402

PASSWORD = os.environ.get("LEDGER_PASSWORD", "")
_fails: dict[str, list] = {}


def _session_token() -> str:
    key_file = DATA_DIR / ".session_key"
    if not key_file.exists():
        key_file.write_bytes(secrets.token_bytes(32))
        key_file.chmod(0o600)
    return hmac.new(key_file.read_bytes(), PASSWORD.encode(), hashlib.sha256).hexdigest()


def _signed_in(request: Request) -> bool:
    cookie = request.cookies.get("ledger_session", "")
    if cookie and hmac.compare_digest(cookie, _session_token()):
        return True
    sent = request.headers.get("authorization", "")
    sent = sent[7:] if sent.lower().startswith("bearer ") else request.headers.get("x-ledger-key", "")
    return bool(sent) and hmac.compare_digest(sent.encode(), PASSWORD.encode())


@app.middleware("http")
async def require_password(request: Request, call_next):
    path = request.url.path
    if PASSWORD and not (path in ("/login", "/api/login", "/healthz") or path.startswith("/static/")) and not _signed_in(request):
        if path == "/":
            return RedirectResponse("/login")
        return JSONResponse({"detail": "Sign in first."}, status_code=401)
    return await call_next(request)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/login")
def login_page():
    return FileResponse(STATIC_DIR / "login.html") if PASSWORD else RedirectResponse("/")


@app.post("/api/login")
async def login(request: Request, b: dict = Body(...)):
    who = request.client.host if request.client else "?"
    recent = [t for t in _fails.get(who, []) if _time.time() - t < 300]
    if len(recent) >= 8:
        raise HTTPException(429, "Too many attempts - wait a few minutes.")
    if not PASSWORD or not hmac.compare_digest(str(b.get("password", "")).encode(), PASSWORD.encode()):
        _fails[who] = recent + [_time.time()]
        await asyncio.sleep(1.0)
        raise HTTPException(401, "Wrong password.")
    _fails.pop(who, None)
    resp = JSONResponse({"ok": True})
    https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    resp.set_cookie("ledger_session", _session_token(), max_age=60 * 60 * 24 * 60, httponly=True, samesite="lax", secure=https)
    return resp


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("ledger_session")
    return resp


@app.get("/api/access")
def access(request: Request):
    """Who can reach the books: is a password set, and does this request come from outside the machine?"""
    import ipaddress
    host = request.client.host if request.client else ""
    try:
        ip = ipaddress.ip_address(host)
        where = "this computer" if ip.is_loopback else "a private network" if (ip.is_private or ip in ipaddress.ip_network("100.64.0.0/10")) else "the internet"
    except ValueError:
        where = "unknown"
    return {"password": bool(PASSWORD), "from": where, "docker": Path("/.dockerenv").exists()}


app.router.routes.extend(mcp_app.routes)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ------------------------------------------------------------------ first-run setup, model presets and test

def _has_books(c) -> bool:
    return bool(c.execute("SELECT 1 FROM transactions LIMIT 1").fetchone() or c.execute("SELECT 1 FROM receipts LIMIT 1").fetchone()
                or c.execute("SELECT 1 FROM invoices LIMIT 1").fetchone())


@app.get("/api/setup")
def setup_state():
    s = get_settings()
    with db(readonly=True) as c:
        empty = not _has_books(c)
    return {"needed": s.get("setup_done") != "1" and empty, "empty": empty, "packs": packs.public(), "pack": packs.pack_id(s),
            "presets": llm_presets(), "docker": Path("/.dockerenv").exists()}


SETUP_KEYS = ("books_type", "company_name", "owner_names", "country", "country_pack", "base_currency", "vat_registered",
              "llm_provider", "llm_base_url", "llm_fallback_url", "llm_model", "llm_api_key", "llm_vision")


@app.post("/api/setup")
def setup_apply(b: dict = Body(...)):
    """Save the answers of the setup screen. While the ledger is still empty, the starter categories (keywords,
    descriptions) follow the chosen country; once there are books, categories are left alone."""
    values = {k: b[k] for k in SETUP_KEYS if k in b and not (k == "llm_api_key" and not b[k])}
    if values.get("base_currency"):
        values["base_currency"] = str(values["base_currency"]).upper().strip()[:3]
    values["setup_done"] = "1"
    set_settings(values)
    _status_cache["at"] = 0.0
    applied = False
    with db() as c:
        if not _has_books(c) and not c.execute("SELECT 1 FROM rules LIMIT 1").fetchone():
            c.execute("DELETE FROM categories")
            c.executemany("INSERT INTO categories(name, kind, description, keywords) VALUES (?,?,?,?)",
                          packs.categories_for(packs.pack_id(get_settings())))
            c.execute("UPDATE accounts SET name = ?, currency = ? WHERE kind = 'cash' AND NOT EXISTS (SELECT 1 FROM transactions)",
                      (packs.pack()["cash_account"], get_settings()["base_currency"]))
            applied = True
    return {"ok": True, "categories_from_pack": applied, "settings": public_settings()}


@app.post("/api/setup/skip")
def setup_skip():
    set_settings({"setup_done": "1"})
    return {"ok": True}


@app.get("/api/llm/presets")
def llm_presets():
    from .llm import PRESETS
    out = []
    for p in PRESETS:
        p = dict(p)
        if Path("/.dockerenv").exists() and p["kind"] == "local":   # inside a container, 127.0.0.1 is the container itself
            p["url"] = p["url"].replace("127.0.0.1", "host.docker.internal")
        out.append(p)
    return out


@app.post("/api/llm/test")
async def llm_test(b: dict = Body(default={})):
    """Try a model server before (or after) saving it. An empty key with the saved address uses the saved key."""
    from .llm import probe
    s = get_settings()
    url = (b.get("base_url") or s["llm_base_url"]).strip()
    key = (b.get("api_key") or "").strip() or (s["llm_api_key"] if url.rstrip("/") == s["llm_base_url"].rstrip("/") else "")
    return await probe(url, key, (b.get("model") if "model" in b else s["llm_model"]) or "")


@app.exception_handler(LLMError)
async def llm_error(_, e: LLMError):
    return JSONResponse({"detail": str(e)}, status_code=503)


@app.exception_handler(ValueError)
async def value_error(_, e: ValueError):
    return JSONResponse({"detail": str(e)}, status_code=422)


def save_upload(f: UploadFile, kind: str) -> Path:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", f.filename or "upload")[-80:]
    folder = UPLOAD_DIR / kind / datetime.now().strftime("%Y-%m")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{datetime.now():%Y%m%d-%H%M%S-%f}_{name}"
    with path.open("wb") as out:
        shutil.copyfileobj(f.file, out)
    if path.suffix.lower() in (".heic", ".heif"):   # iPhone photos: keep a JPEG, which every browser can show
        from .documents import heic_to_jpeg
        jpg = heic_to_jpeg(path)
        path.unlink()
        return jpg
    return path


rel = store.rel


# ------------------------------------------------------------------ pages & files

@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/files/reports/{name}")
def report_file(name: str):
    p = (REPORT_DIR / name).resolve()
    if p.parent != REPORT_DIR.resolve() or not p.exists():
        raise HTTPException(404)
    return FileResponse(p, filename=name)


@app.get("/files/uploads/{path:path}")
def upload_file(path: str):
    p = (UPLOAD_DIR / path).resolve()
    if UPLOAD_DIR.resolve() not in p.parents or not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


# ------------------------------------------------------------------ status & settings

_status_cache = {"at": 0.0, "llm": None, "ocr": None}


async def _llm_status() -> dict:
    try:
        llm = await LLM().status(connect=2.0)
        llm["ok"] = bool(llm["using"])
    except LLMError as e:
        llm = {"ok": False, "error": str(e)}
    return llm


@app.get("/api/status")
async def status(fresh: bool = False):
    """Model + OCR state for the header. Both are asked at the same time and the answer is kept for 15 s, so an
    offline model server costs ~2 s once instead of 13 s on every page."""
    import time
    if fresh or time.monotonic() - _status_cache["at"] > 15:
        llm, o = await asyncio.gather(_llm_status(), ocr.status())
        _status_cache.update(at=time.monotonic(), llm=llm, ocr=o)
    with db() as c:
        counts = {t: c.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
                  for t in ("transactions", "receipts", "invoices", "accounts")}
    return {"llm": _status_cache["llm"], "ocr": _status_cache["ocr"], "counts": counts, "settings": public_settings()}


SECRETS = ("llm_api_key",)


def public_settings() -> dict:
    """Settings as sent to browsers: secrets are never sent back, only whether they are set."""
    s = get_settings()
    for k in SECRETS:
        s[k + "_set"] = bool(s.get(k))
        s[k] = ""
    return s


@app.get("/api/settings")
def read_settings():
    return public_settings()


@app.put("/api/settings")
def write_settings(values: dict = Body(...)):
    for k in SECRETS:  # empty = keep the stored secret; "-" clears it
        if not values.get(k):
            values.pop(k, None)
        elif values[k].strip() == "-":
            values[k] = ""
    # internal bookkeeping of the app itself is not edited through the settings form
    values = {k: v for k, v in values.items() if not k.endswith("_set") and k not in ("llm_quirks", "setup_done", "household_categories_added")}
    old = get_settings()["base_currency"].upper()
    if "base_currency" in values:
        values["base_currency"] = (values["base_currency"] or "DKK").upper().strip()[:3]
    set_settings(values)
    _status_cache["at"] = 0.0
    if values.get("base_currency", old) != old:
        recompute(force=True)  # every amount must be re-expressed in the new base currency
    return public_settings()


# ------------------------------------------------------------------ categories, rules, accounts

@app.get("/api/categories")
def list_categories():
    return categories()


@app.post("/api/categories")
def add_category(c: dict = Body(...)):
    with db() as conn:
        conn.execute("INSERT INTO categories(name, kind, description, keywords) VALUES (?,?,?,?)",
                     (c["name"].strip(), c.get("kind", "expense"), c.get("description", ""), c.get("keywords", "")))
    return categories()


@app.patch("/api/categories/{cid}")
def edit_category(cid: int, c: dict = Body(...)):
    with db() as conn:
        old = conn.execute("SELECT name FROM categories WHERE id = ?", (cid,)).fetchone()
        if not old:
            raise HTTPException(404)
        fields = {k: c[k] for k in ("name", "kind", "description", "keywords") if k in c}
        if fields:
            conn.execute(f"UPDATE categories SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?",
                         [*fields.values(), cid])
        if "name" in fields and fields["name"] != old["name"]:
            for t in ("transactions", "receipts", "invoices", "rules"):
                conn.execute(f"UPDATE {t} SET category = ? WHERE category = ?", (fields["name"], old["name"]))
    return categories()


@app.delete("/api/categories/{cid}")
def delete_category(cid: int):
    with db() as conn:
        old = conn.execute("SELECT name FROM categories WHERE id = ?", (cid,)).fetchone()
        if not old or old["name"] == "Uncategorized":
            raise HTTPException(400, "This category cannot be deleted")
        for t in ("transactions", "receipts", "invoices"):
            conn.execute(f"UPDATE {t} SET category = 'Uncategorized' WHERE category = ?", (old["name"],))
        conn.execute("DELETE FROM rules WHERE category = ?", (old["name"],))
        conn.execute("DELETE FROM categories WHERE id = ?", (cid,))
    return categories()


@app.get("/api/rules")
def list_rules():
    with db() as c:
        return c.execute("SELECT * FROM rules ORDER BY category, pattern").fetchall()


@app.post("/api/rules")
def add_rule(r: dict = Body(...)):
    with db() as c:
        c.execute("INSERT INTO rules(pattern, category, created_at) VALUES (?,?,?) ON CONFLICT(pattern) "
                  "DO UPDATE SET category = excluded.category", (r["pattern"].strip().lower(), r["category"], now()))
    return list_rules()


@app.delete("/api/rules/{rid}")
def delete_rule(rid: int):
    with db() as c:
        c.execute("DELETE FROM rules WHERE id = ?", (rid,))
    return list_rules()


@app.get("/api/accounts")
def list_accounts():
    with db() as c:
        rows = _accounts_query(c)
        kc = cards.all_cards(c)
    for a in rows:
        a["cards"] = [k for k in kc if k["account_id"] == a["id"]]
    return rows


def _accounts_query(c):
    return c.execute("SELECT a.*, (SELECT COUNT(*) FROM transactions t WHERE t.account_id = a.id) AS n, "
                     "(SELECT balance_after FROM transactions t WHERE t.account_id = a.id AND balance_after IS NOT NULL "
                     " ORDER BY date DESC, id DESC LIMIT 1) AS last_balance FROM accounts a ORDER BY name").fetchall()


@app.post("/api/accounts")
def add_account(a: dict = Body(...)):
    with db() as c:
        c.execute("INSERT INTO accounts(name, iban, currency, kind, created_at) VALUES (?,?,?,?,?)",
                  (a["name"], a.get("iban", ""), a.get("currency", ""), a.get("kind", "bank"), now()))
    return list_accounts()


@app.delete("/api/accounts/{aid}")
def delete_account(aid: int):
    """Delete a bank account with its transactions, statements and linked cards (the cash book can't be deleted)."""
    with db() as c:
        acc = c.execute("SELECT * FROM accounts WHERE id = ?", (aid,)).fetchone()
        if not acc:
            raise HTTPException(404)
        if acc["kind"] == "cash":
            raise HTTPException(400, "The cash book can't be deleted - delete its entries in the Cash tab instead")
        stmts = [r["id"] for r in c.execute("SELECT id FROM imports WHERE account_id = ?", (aid,))]
        n = sum(_delete_import(c, i) for i in stmts)
        n += _delete_transactions(c, "account_id = ?", (aid,))   # manual / pasted transactions
        c.execute("DELETE FROM account_cards WHERE account_id = ?", (aid,))
        c.execute("DELETE FROM accounts WHERE id = ?", (aid,))
    return {"deleted_transactions": n, "deleted_statements": len(stmts)}


@app.patch("/api/accounts/{aid}")
def edit_account(aid: int, a: dict = Body(...)):
    fields = {k: a[k] for k in ("name", "iban", "currency", "kind") if k in a}
    with db() as c:
        c.execute(f"UPDATE accounts SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?", [*fields.values(), aid])
    return list_accounts()


# ------------------------------------------------------------------ bank statements

@app.post("/api/statements/parse")
async def statement_parse(file: UploadFile = File(...), account_id: int | None = Form(None)):
    path = save_upload(file, "statements")
    result = await parse_statement(path, account_id)
    meta = result["meta"]
    with db() as c:
        iid = c.execute("INSERT INTO imports(kind, filename, file_path, account_id, status, meta, created_at) "
                        "VALUES ('statement',?,?,?,?,?,?)",
                        (file.filename, rel(path), meta.get("account_id"), "preview",
                         json.dumps({k: v for k, v in meta.items() if k != "mapping"} | {"warnings": result["warnings"]}),
                         now())).lastrowid
    return {"import_id": iid, **result}


@app.post("/api/statements/commit")
def statement_commit(body: dict = Body(...)):
    rows = body.get("rows") or []
    account_id = body.get("account_id")
    with db() as c:
        if not account_id and body.get("new_account"):
            na = body["new_account"]
            account_id = c.execute("INSERT INTO accounts(name, iban, currency, created_at) VALUES (?,?,?,?)",
                                   (na.get("name") or "Bank account", na.get("iban", ""), na.get("currency", ""), now())).lastrowid
        add_fingerprints(rows, account_id)
        inserted = 0
        for r in rows:
            r["amount"] = float(r["amount"])
            r["currency"] = (r.get("currency") or base_currency()).upper()
            r.pop("fx_source", None)
            tx_fx(r)
            cur = c.execute(
                "INSERT OR IGNORE INTO transactions(date, amount, currency, description, counterparty, reference, category, "
                "category_source, account_id, import_id, balance_after, fingerprint, created_at, orig_amount, orig_currency, "
                "fx_rate, fx_source, amount_base) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r["date"], r["amount"], r["currency"], r.get("description", ""),
                 r.get("counterparty", ""), r.get("reference", ""), r.get("category") or "Uncategorized",
                 r.get("category_source", ""), account_id, body.get("import_id"), r.get("balance_after"),
                 r["fingerprint"], now(), r.get("orig_amount"), r.get("orig_currency") or "", r["fx_rate"], r["fx_source"],
                 r["amount_base"]))
            inserted += cur.rowcount
        c.execute("UPDATE imports SET status = 'imported', account_id = ? WHERE id = ?", (account_id, body.get("import_id")))
    for r in rows:
        if r.get("remember") and r.get("category") not in (None, "", "Uncategorized"):
            learn_rule(r.get("counterparty", ""), r.get("description", ""), r["category"])
    matched = auto_match()
    return {"inserted": inserted, "skipped": len(rows) - inserted, "account_id": account_id, "matched_documents": matched}


@app.get("/api/statements")
def statement_list():
    with db() as c:
        rows = c.execute(
            "SELECT i.*, a.name AS account, (SELECT COUNT(*) FROM transactions t WHERE t.import_id = i.id) AS n, "
            "(SELECT MIN(date) FROM transactions t WHERE t.import_id = i.id) AS first, "
            "(SELECT MAX(date) FROM transactions t WHERE t.import_id = i.id) AS last "
            "FROM imports i LEFT JOIN accounts a ON a.id = i.account_id WHERE i.kind = 'statement' "
            "ORDER BY (i.status = 'imported') DESC, i.id DESC").fetchall()
    for r in rows:
        r["meta"] = loads(r["meta"], {})
    return rows


def _delete_transactions(c, where: str, args: tuple) -> int:
    """Delete transactions; receipts/invoices matched to them stay but are unlinked."""
    ids = [r["id"] for r in c.execute(f"SELECT id FROM transactions WHERE {where}", args)]
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        c.execute(f"UPDATE receipts SET transaction_id = NULL WHERE transaction_id IN ({q})", chunk)
        c.execute(f"UPDATE invoices SET transaction_id = NULL, status = 'unpaid' WHERE transaction_id IN ({q})", chunk)
        c.execute(f"DELETE FROM transactions WHERE id IN ({q})", chunk)
    return len(ids)


def _delete_import(c, iid: int) -> int:
    imp = c.execute("SELECT file_path FROM imports WHERE id = ?", (iid,)).fetchone()
    n = _delete_transactions(c, "import_id = ?", (iid,))
    c.execute("DELETE FROM imports WHERE id = ?", (iid,))
    if imp and imp["file_path"]:
        f = (UPLOAD_DIR / imp["file_path"]).resolve()
        if UPLOAD_DIR.resolve() in f.parents:
            f.unlink(missing_ok=True)   # the uploaded statement file goes too
    return n


@app.delete("/api/statements/{iid}")
def statement_delete(iid: int):
    with db() as c:
        n = _delete_import(c, iid)
    return {"deleted_transactions": n}


# ------------------------------------------------------------------ transactions

@app.get("/api/transactions")
def tx_list(q: str = "", start: str = "", end: str = "", category: str = "", account_id: int | None = None,
            only: str = "", limit: int = 200, offset: int = 0):
    sql = (" FROM transactions t LEFT JOIN accounts a ON a.id = t.account_id LEFT JOIN categories k ON k.name = t.category "
           "WHERE 1=1")
    args = []
    if q:
        sql += " AND (t.description LIKE ? OR t.counterparty LIKE ? OR t.reference LIKE ? OR t.notes LIKE ?)"
        args += [f"%{q}%"] * 4
    if start:
        sql += " AND t.date >= ?"
        args.append(start)
    if end:
        sql += " AND t.date <= ?"
        args.append(end)
    if category:
        sql += " AND t.category = ?"
        args.append(category)
    if account_id:
        sql += " AND t.account_id = ?"
        args.append(account_id)
    if only == "uncategorized":
        sql += " AND t.category = 'Uncategorized'"
    elif only == "undocumented":
        sql += (" AND t.amount < 0 AND t.id NOT IN (SELECT transaction_id FROM receipts WHERE transaction_id IS NOT NULL) "
                "AND t.id NOT IN (SELECT transaction_id FROM invoices WHERE transaction_id IS NOT NULL)")
    with db() as c:
        total = c.execute("SELECT COUNT(*) AS n, COALESCE(SUM(t.amount), 0) AS s" + sql, args).fetchone()
        rows = c.execute(
            "SELECT t.*, a.name AS account, k.kind AS kind, "
            "(SELECT id FROM receipts r WHERE r.transaction_id = t.id LIMIT 1) AS receipt_id, "
            "(SELECT id FROM invoices i WHERE i.transaction_id = t.id LIMIT 1) AS invoice_id" + sql +
            " ORDER BY t.date DESC, t.id DESC LIMIT ? OFFSET ?", args + [min(limit, 2000), offset]).fetchall()
    return {"total": total["n"], "sum": round(total["s"], 2), "rows": rows}


@app.post("/api/transactions")
def tx_add(t: dict = Body(...)):
    return tools.add_transactions([t], t.get("account_name"))


@app.patch("/api/transactions/{tid}")
def tx_edit(tid: int, t: dict = Body(...)):
    fields = {k: t[k] for k in ("date", "amount", "currency", "description", "counterparty", "category", "notes") if k in t}
    if "category" in fields:
        fields["category_source"] = "user"
    with db() as c:
        if fields:
            c.execute(f"UPDATE transactions SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?", [*fields.values(), tid])
        row = c.execute("SELECT * FROM transactions WHERE id = ?", (tid,)).fetchone()
        if "fx_rate" in t or {"date", "amount", "currency"} & set(fields):
            if "fx_rate" in t:  # a number = manual rate, null = back to the ECB rate
                row["fx_rate"], row["fx_source"] = (float(t["fx_rate"]), "manual") if t["fx_rate"] else (None, "")
            tx_fx(row)
            c.execute("UPDATE transactions SET fx_rate=?, fx_source=?, amount_base=? WHERE id=?",
                      (row["fx_rate"], row["fx_source"], row["amount_base"], tid))
    learned = None
    if t.get("remember") and "category" in t and t["category"] != "Uncategorized":
        learned = learn_rule(row["counterparty"], row["description"], t["category"])
    return {"row": row, "rule": learned}


@app.delete("/api/transactions/{tid}")
def tx_delete(tid: int):
    with db() as c:
        c.execute("UPDATE receipts SET transaction_id = NULL WHERE transaction_id = ?", (tid,))
        c.execute("UPDATE invoices SET transaction_id = NULL, status = 'unpaid' WHERE transaction_id = ?", (tid,))
        c.execute("DELETE FROM transactions WHERE id = ?", (tid,))
    return {"ok": True}


@app.post("/api/transactions/recategorize")
async def tx_recategorize(body: dict = Body(default={})):
    """scope 'uncategorized' (default) or 'weak' = also re-check what the model earlier left in Other expenses / Other income.
    use_llm=false applies only rules, keywords and earlier choices (works with the model offline)."""
    from .categorize import recategorize
    return await recategorize(body.get("scope") or "uncategorized", use_llm=body.get("use_llm", True))


@app.get("/api/transactions/open-counts")
def tx_open_counts():
    with db(readonly=True) as c:
        one = lambda q: c.execute(q).fetchone()["n"]  # noqa: E731
        return {"uncategorized": one("SELECT COUNT(*) AS n FROM transactions WHERE category = 'Uncategorized'"),
                "weak": one("SELECT COUNT(*) AS n FROM transactions WHERE category IN ('Other expenses', 'Other income') "
                            "AND category_source IN ('', 'llm')")}


# ------------------------------------------------------------------ receipts

def _receipt_row(rid):
    with db() as c:
        r = c.execute("SELECT r.*, t.date AS tx_date, t.amount AS tx_amount, t.description AS tx_description, a.kind AS tx_account_kind "
                      "FROM receipts r LEFT JOIN transactions t ON t.id = r.transaction_id LEFT JOIN accounts a ON a.id = t.account_id "
                      "WHERE r.id = ?", (rid,)).fetchone()
    if r:
        r["items"], r["vat_lines"], r["raw"] = loads(r["items"], []), loads(r["vat_lines"], []), loads(r["raw"], {})
        with db() as c:
            acc = cards.account_for(c, r.get("card_last4") or "")
            r["card_account"] = acc["name"] if acc else None
            r["why_unmatched"] = None if r["transaction_id"] else cards.why_unmatched(c, r)
    return r


_doc_fx_update = store.doc_fx_update


@app.post("/api/receipts/upload")
async def receipt_upload(file: UploadFile = File(...)):
    path = save_upload(file, "receipts")
    # No model right now (or it was offline a moment ago): keep the receipt in the queue, it is read when the model is back.
    reason = upload_queue.known_offline()
    if not reason:
        try:
            d = await extract_document(path, "receipt")
        except LLMOffline as e:
            reason = str(e)
            upload_queue.mark_offline(reason)
    if reason:
        qid = upload_queue.add(path, file.filename, reason)
        n = upload_queue.waiting_count()
        why = "a vision model has to be loaded first" if "cannot read images" in reason else "the model is offline"
        return {"queued": True, "queue_id": qid, "reason": reason, "waiting": n,
                "message": f"Saved - {why}, so it will be read later ({n} waiting)."}
    rid, booked = store.save_receipt(path, file.filename, d)
    auto_match()
    r = _receipt_row(rid)
    # one ready-made sentence for clients that just show a notification (e.g. the iPhone shortcut)
    msg = f"{r.get('merchant') or 'Receipt'}: {r['total'] if r.get('total') is not None else '?'} {r.get('currency') or ''}"
    msg += f", {r['date']}" if r.get("date") else ""
    msg += " - cash book" if booked else (" - matched to bank" if r.get("transaction_id") else "")
    if d["warnings"]:
        msg += f" (check it: {len(d['warnings'])} warning{'s' if len(d['warnings']) > 1 else ''})"
    return {"receipt": r, "warnings": d["warnings"], "document_type": d.get("document_type"), "cash": booked, "message": msg}


@app.get("/api/receipts/queue")
def receipt_queue():
    return {"items": upload_queue.items(), "running": upload_queue.STATE["running"], "offline": upload_queue.known_offline()}


@app.post("/api/receipts/queue/process")
async def receipt_queue_process():
    """'Try now': read the waiting receipts if the model answers."""
    upload_queue.STATE["offline_until"] = 0
    if not await upload_queue.model_ready():
        return {"done": 0, "errors": 0, "waiting": upload_queue.waiting_count(), "offline": upload_queue.known_offline()}
    return await upload_queue.process()


@app.post("/api/receipts/queue/{qid}/retry")
def receipt_queue_retry(qid: int):
    with db() as c:
        c.execute("UPDATE upload_queue SET status = 'waiting', reason = '' WHERE id = ? AND status = 'error'", (qid,))
    return {"ok": True}


@app.delete("/api/receipts/queue/{qid}")
def receipt_queue_delete(qid: int):
    with db() as c:
        q = c.execute("SELECT * FROM upload_queue WHERE id = ?", (qid,)).fetchone()
        if not q:
            raise HTTPException(404)
        if q["status"] == "reading":
            raise HTTPException(409, "It is being read right now")
        c.execute("DELETE FROM upload_queue WHERE id = ?", (qid,))
    if q["status"] != "done":               # never read: nothing else points at the file
        (UPLOAD_DIR / q["file_path"]).unlink(missing_ok=True)
    return {"ok": True}


@app.get("/api/receipts")
def receipt_list(status: str = "", q: str = ""):
    sql = ("SELECT r.id, r.date, r.merchant, r.total, r.vat, r.currency, r.total_base, r.fx_source, r.category, r.payment_method, r.status, r.confidence, "
           "r.file_path, r.original_name, r.transaction_id, t.date AS tx_date, t.amount AS tx_amount, a.kind AS tx_account_kind "
           "FROM receipts r LEFT JOIN transactions t ON t.id = r.transaction_id LEFT JOIN accounts a ON a.id = t.account_id WHERE 1=1")
    args = []
    if status:
        sql += " AND r.status = ?"
        args.append(status)
    if q:
        sql += " AND (r.merchant LIKE ? OR r.notes LIKE ?)"
        args += [f"%{q}%"] * 2
    with db() as c:
        return c.execute(sql + " ORDER BY COALESCE(r.date, r.created_at) DESC, r.id DESC LIMIT 1000", args).fetchall()


@app.get("/api/receipts/{rid}")
def receipt_get(rid: int):
    r = _receipt_row(rid)
    if not r:
        raise HTTPException(404)
    return r


@app.patch("/api/receipts/{rid}")
def receipt_edit(rid: int, r: dict = Body(...)):
    fields = {k: r[k] for k in ("date", "merchant", "merchant_tax_id", "total", "net", "vat", "currency", "payment_method",
                                "category", "status", "notes") if k in r}
    if "card_last4" in r:
        fields["card_last4"] = cards.norm(r["card_last4"])
    with db() as c:
        if fields:
            c.execute(f"UPDATE receipts SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?", [*fields.values(), rid])
        _doc_fx_update(c, "receipts", rid, r.get("fx_rate"))
        cash.sync_receipt(c, rid)
    auto_match()
    return _receipt_row(rid)


@app.delete("/api/receipts/{rid}")
def receipt_delete(rid: int):
    with db() as c:
        c.execute("DELETE FROM receipts WHERE id = ?", (rid,))
        cash.sync_receipt(c, rid)  # removes its cash-book entry, if any
    return {"ok": True}


@app.post("/api/receipts/{rid}/to-invoice")
def receipt_to_invoice(rid: int):
    r = _receipt_row(rid)
    raw = r["raw"] or {}
    direction = raw.get("direction") if raw.get("direction") in ("sales", "purchase") else "purchase"
    with db() as c:
        iid = c.execute(
            "INSERT INTO invoices(direction, number, counterparty, counterparty_tax_id, issue_date, due_date, net, vat, total, "
            "currency, category, status, file_path, original_name, vat_lines, notes, raw, created_at, transaction_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (direction, raw.get("invoice_number") or "", r["merchant"] if direction == "purchase" else (raw.get("buyer_name") or ""),
             r["merchant_tax_id"] if direction == "purchase" else (raw.get("buyer_tax_id") or ""),
             r["date"], raw.get("due_date"), r["net"], r["vat"], r["total"], r["currency"], r["category"],
             "paid" if r["transaction_id"] else "unpaid", r["file_path"], r["original_name"], json.dumps(r["vat_lines"]),
             r["notes"], json.dumps(raw), now(), r["transaction_id"])).lastrowid
        _doc_fx_update(c, "invoices", iid)
        c.execute("DELETE FROM receipts WHERE id = ?", (rid,))
    auto_match()
    return {"invoice_id": iid}


# ------------------------------------------------------------------ payment cards (last 4 digits)

@app.get("/api/cards")
def cards_list():
    with db() as c:
        return {"cards": cards.all_cards(c), "unknown": cards.unknown(c), "ignored": cards.ignored(c)}


@app.post("/api/cards/ignore")
def cards_ignore(b: dict = Body(...)):
    """A card that is not on any of your accounts (e.g. someone else's): stop asking about it and never match its receipts."""
    with db() as c:
        cards.ignore(c, b.get("last4", ""), b.get("note") or "")
    return {"ok": True}


@app.delete("/api/cards/ignore/{last4}")
def cards_unignore(last4: str):
    with db() as c:
        c.execute("DELETE FROM ignored_cards WHERE last4 = ?", (cards.norm(last4),))
    auto_match()
    return {"ok": True}


@app.post("/api/cards")
def cards_add(b: dict = Body(...)):
    with db() as c:
        if not c.execute("SELECT 1 FROM accounts WHERE id = ?", (b.get("account_id"),)).fetchone():
            raise HTTPException(400, "Choose an account")
        cid = cards.add(c, int(b["account_id"]), b.get("last4", ""), b.get("label") or "")
    linked = auto_match()  # receipts paid with this card can now be matched within its account
    return {"id": cid, "matched": linked}


@app.patch("/api/cards/{cid}")
def cards_edit(cid: int, b: dict = Body(...)):
    with db() as c:
        if "label" in b:
            c.execute("UPDATE account_cards SET label = ? WHERE id = ?", ((b["label"] or "")[:60], cid))
    return {"ok": True}


@app.delete("/api/cards/{cid}")
def cards_delete(cid: int):
    with db() as c:
        c.execute("DELETE FROM account_cards WHERE id = ?", (cid,))
    return {"ok": True}


# ------------------------------------------------------------------ cash book

@app.get("/api/cash")
def cash_book(start: str = "", end: str = ""):
    with db() as c:
        return cash.book(c, start, end)


@app.post("/api/cash")
def cash_record(b: dict = Body(...)):
    """{date, amount (>0 in, <0 out), description, category, currency?, counterparty?, source_tx_id?}"""
    with db() as c:
        tid = cash.record(c, b["date"], float(b["amount"]), b.get("description") or "", b.get("category") or "Uncategorized",
                          b.get("currency"), b.get("counterparty") or "", b.get("source_tx_id"), b.get("notes") or "")
    if float(b["amount"]) < 0:
        auto_match()  # a receipt that arrives later can still be linked to this cash expense
    return {"id": tid}


# ------------------------------------------------------------------ email inboxes

def _account_out(a: dict) -> dict:
    a = dict(a)
    a["has_password"] = bool(a.pop("password", None))
    return a


@app.get("/api/email/presets")
def email_presets():
    return mail.PRESETS


@app.get("/api/email/accounts")
def email_accounts():
    return [_account_out(a) for a in mail.accounts()]


ACCOUNT_FIELDS = ("name", "preset", "host", "port", "security", "username", "password", "folders", "since_days", "enabled")


@app.post("/api/email/accounts")
def email_account_add(a: dict = Body(...)):
    preset = mail.PRESETS.get(a.get("preset") or "other", mail.PRESETS["other"])
    a.setdefault("host", preset["host"])
    a.setdefault("port", preset["port"])
    if not (a.get("host") and a.get("username") and a.get("password")):
        raise HTTPException(400, "Server, email address and password are required")
    if a.get("security", "ssl") not in ("ssl", "starttls", "none"):
        raise HTTPException(400, "security must be ssl, starttls or none")
    a.setdefault("name", a["username"])
    fields = {k: a[k] for k in ACCOUNT_FIELDS if k in a}
    with db() as c:
        aid = c.execute(f"INSERT INTO email_accounts({', '.join(fields)}, created_at) VALUES ({', '.join('?' * len(fields))}, ?)",
                        [*fields.values(), now()]).lastrowid
        return _account_out(c.execute("SELECT * FROM email_accounts WHERE id = ?", (aid,)).fetchone())


@app.patch("/api/email/accounts/{aid}")
def email_account_edit(aid: int, a: dict = Body(...)):
    fields = {k: a[k] for k in ACCOUNT_FIELDS if k in a and not (k == "password" and not a[k])}  # empty password = keep
    with db() as c:
        if fields:
            c.execute(f"UPDATE email_accounts SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?", [*fields.values(), aid])
        row = c.execute("SELECT * FROM email_accounts WHERE id = ?", (aid,)).fetchone()
    if not row:
        raise HTTPException(404)
    return _account_out(row)


@app.delete("/api/email/accounts/{aid}")
def email_account_delete(aid: int):
    """Removes the account and its scan history. Receipts and invoices it found stay in the books."""
    with db() as c:
        c.execute("DELETE FROM email_items WHERE account_id = ?", (aid,))
        c.execute("DELETE FROM email_accounts WHERE id = ?", (aid,))
    return {"ok": True}


@app.post("/api/email/accounts/{aid}/test")
async def email_account_test(aid: int):
    acc = next((a for a in mail.accounts() if a["id"] == aid), None)
    if not acc:
        raise HTTPException(404)
    try:
        folders = await asyncio.to_thread(mail.list_folders, acc)
    except mail.MailError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "folders": folders}


@app.post("/api/email/scan")
async def email_scan(b: dict = Body(default={})):
    if mail.STATE["running"]:
        return {"started": False, "state": mail.STATE}
    asyncio.create_task(mail.run(b.get("account_id")))
    await asyncio.sleep(0.2)
    return {"started": True, "state": mail.STATE}


@app.get("/api/email/status")
def email_status():
    return {"state": mail.STATE, "counts": mail.counts(), "accounts": [_account_out(a) for a in mail.accounts()],
            "scan_hours": get_settings().get("email_scan_hours")}


@app.get("/api/email/items")
def email_items(status: str = "", limit: int = 100):
    q = "SELECT * FROM email_items WHERE status != 'ignored'"
    args = []
    if status:
        q += " AND status = ?"
        args.append(status)
    with db() as c:
        return c.execute(q + " ORDER BY COALESCE(processed_at, created_at) DESC, id DESC LIMIT ?", args + [min(limit, 500)]).fetchall()


@app.post("/api/email/items/{iid}/{action}")
def email_item_action(iid: int, action: str):
    if action not in ("retry", "skip"):
        raise HTTPException(400)
    with db() as c:
        c.execute("UPDATE email_items SET status = ?, reason = ? WHERE id = ?",
                  ("pending" if action == "retry" else "skipped", "" if action == "retry" else "skipped by you", iid))
    return {"ok": True}


# ------------------------------------------------------------------ invoices

def _invoice_rows(where="1=1", args=()):
    with db() as c:
        rows = c.execute("SELECT i.*, t.date AS paid_date FROM invoices i LEFT JOIN transactions t ON t.id = i.transaction_id "
                         f"WHERE {where} ORDER BY i.issue_date DESC, i.id DESC", args).fetchall()
    today = date.today().isoformat()
    for r in rows:
        r["vat_lines"], r["raw"] = loads(r["vat_lines"], []), loads(r["raw"], {})
        r["overdue"] = r["status"] == "unpaid" and bool(r["due_date"]) and r["due_date"] < today
    return rows


@app.get("/api/invoices")
def invoice_list():
    return _invoice_rows()


@app.post("/api/invoices/upload")
async def invoice_upload(file: UploadFile = File(...), direction: str = Form("")):
    path = save_upload(file, "invoices")
    d = await extract_document(path, "invoice")
    iid = store.save_invoice(path, file.filename, d, direction)
    auto_match()
    return {"invoice": _invoice_rows("i.id = ?", (iid,))[0], "warnings": d["warnings"]}


@app.post("/api/invoices")
def invoice_add(i: dict = Body(...)):
    return tools.save_invoice(**{k: i[k] for k in ("direction", "counterparty", "issue_date", "total", "number", "due_date",
                                                   "net", "vat", "currency", "category", "status", "notes") if k in i})


@app.patch("/api/invoices/{iid}")
def invoice_edit(iid: int, i: dict = Body(...)):
    fields = {k: i[k] for k in ("direction", "number", "counterparty", "counterparty_tax_id", "issue_date", "due_date", "net",
                                "vat", "total", "currency", "category", "status", "notes") if k in i}
    with db() as c:
        if fields:
            c.execute(f"UPDATE invoices SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?", [*fields.values(), iid])
        _doc_fx_update(c, "invoices", iid, i.get("fx_rate"))
    auto_match()
    return _invoice_rows("i.id = ?", (iid,))[0]


@app.delete("/api/invoices/{iid}")
def invoice_delete(iid: int):
    with db() as c:
        c.execute("DELETE FROM invoices WHERE id = ?", (iid,))
    return {"ok": True}


# ------------------------------------------------------------------ reconciliation

@app.get("/api/reconcile")
def reconcile_view():
    s = get_settings()
    with db() as c:
        sug = suggestions(c)[:60]
        miss = missing_documents(c, min_amount=float(s["missing_doc_threshold"] or 0))
        ur = c.execute("SELECT * FROM receipts WHERE transaction_id IS NULL ORDER BY date DESC").fetchall()
        for r in ur:
            r["why"] = cards.why_unmatched(c, r)
            r.pop("raw", None)
        unknown_cards = cards.unknown(c)
        ui = c.execute("SELECT * FROM invoices WHERE transaction_id IS NULL AND status = 'unpaid' ORDER BY issue_date DESC").fetchall()
    return {"suggestions": sug, "missing": miss[:300], "missing_count": len(miss), "unknown_cards": unknown_cards,
            "missing_total": round(-sum(m["amount_base"] if m["amount_base"] is not None else m["amount"] for m in miss), 2), "unmatched_receipts": ur, "open_invoices": ui}


@app.post("/api/reconcile/link")
def reconcile_link(b: dict = Body(...)):
    with db() as c:
        link(c, b["doc_type"], int(b["doc_id"]), b.get("transaction_id"))
    return {"ok": True}


@app.post("/api/reconcile/auto")
def reconcile_auto():
    return {"linked": auto_match()}


# ------------------------------------------------------------------ desktop automation (app/desktop.py)

@app.get("/api/desktop")
def desktop_status():
    nr = desktop.next_run()
    return {"running": desktop.STATE["running"], "phase": desktop.STATE["phase"],
            "next_run": nr.isoformat(timespec="minutes") if nr else None, "runs": desktop.runs(),
            "public_key": desktop.ensure_key()}


@app.get("/api/desktop/setup-script")
def desktop_setup_script():
    return {"script": desktop.setup_script()}


@app.post("/api/desktop/run")
async def desktop_run():
    if desktop.STATE["running"]:
        raise HTTPException(409, "A run is already in progress")
    asyncio.create_task(desktop.run_cycle("manual"))
    await asyncio.sleep(0.2)
    return {"started": True}


@app.post("/api/desktop/cancel")
def desktop_cancel():
    """Stop the current run at its next step (the PC is still powered off if this run woke it)."""
    desktop.STATE["cancel"] = True
    return {"ok": desktop.STATE["running"]}


@app.post("/api/desktop/keep-on")
async def desktop_keep_on():
    """Cancel a power-off that is counting down on the PC."""
    ok, out = await desktop.ssh("cancel")
    if not ok:
        raise HTTPException(502, out)
    return {"ok": True, "output": out}


@app.post("/api/desktop/test")
async def desktop_test():
    return await desktop.test()


# ------------------------------------------------------------------ overview, reports, export

def _todo(c) -> dict:
    """What is waiting for the owner or for the model - shown on the Overview and as counters in the navigation."""
    one = lambda q, a=(): c.execute(q, a).fetchone()["n"]  # noqa: E731
    today = date.today().isoformat()
    return {
        "uncategorized": one("SELECT COUNT(*) AS n FROM transactions WHERE category = 'Uncategorized'"),
        "receipts_to_review": one("SELECT COUNT(*) AS n FROM receipts WHERE status = 'review'"),
        "receipts_unmatched": one("SELECT COUNT(*) AS n FROM receipts WHERE transaction_id IS NULL"),
        "emails_waiting": one("SELECT COUNT(*) AS n FROM email_items WHERE status = 'pending'"),
        "email_errors": one("SELECT COUNT(*) AS n FROM email_items WHERE status = 'error'"),
        "uploads_waiting": one("SELECT COUNT(*) AS n FROM upload_queue WHERE status = 'waiting'"),
        "upload_errors": one("SELECT COUNT(*) AS n FROM upload_queue WHERE status = 'error'"),
        "invoices_unpaid": one("SELECT COUNT(*) AS n FROM invoices WHERE status = 'unpaid'"),
        "invoices_overdue": one("SELECT COUNT(*) AS n FROM invoices WHERE status = 'unpaid' AND due_date != '' AND due_date < ?", (today,)),
        "unknown_cards": len(cards.unknown(c)),
        "rules": one("SELECT COUNT(*) AS n FROM rules"),
    }


@app.get("/api/todo")
def todo():
    with db(readonly=True) as c:
        return _todo(c)


@app.get("/api/overview")
def overview(start: str = "", end: str = ""):
    data = tools.ledger_overview(start or None, end or None)
    with db() as c:
        data["accounts"] = c.execute(
            "SELECT a.id, a.name, a.currency, a.kind, CASE WHEN a.kind = 'cash' THEN "
            "(SELECT ROUND(SUM(amount), 2) FROM transactions t WHERE t.account_id = a.id) ELSE "
            "(SELECT balance_after FROM transactions t WHERE t.account_id = a.id AND "
            "balance_after IS NOT NULL ORDER BY date DESC, id DESC LIMIT 1) END AS balance, "
            "(SELECT MAX(date) FROM transactions t WHERE t.account_id = a.id) AS last_date FROM accounts a").fetchall()
        data["todo"] = _todo(c)
        data["transfers"] = c.execute(
            "SELECT COALESCE(ROUND(SUM(CASE WHEN t.amount > 0 THEN t.amount_base END), 2), 0) AS money_in, "
            "COALESCE(ROUND(-SUM(CASE WHEN t.amount < 0 THEN t.amount_base END), 2), 0) AS money_out, COUNT(*) AS n "
            "FROM transactions t JOIN categories k ON k.name = t.category WHERE k.kind = 'transfer'"
            + (" AND t.date >= ?" if start else "") + (" AND t.date <= ?" if end else ""),
            [x for x in (start, end) if x]).fetchone()
    data["receipts_waiting_for_model"] = upload_queue.waiting_count()
    data["books_type"] = get_settings().get("books_type") or "mixed"
    return data


@app.post("/api/reports/overview")
async def report_overview(b: dict = Body(default={})):
    return await asyncio.to_thread(reports.build_overview, b.get("start") or None, b.get("end") or None, b.get("title") or None)


@app.get("/api/reports")
def report_list():
    with db() as c:
        rows = c.execute("SELECT * FROM reports ORDER BY id DESC LIMIT 200").fetchall()
    out = []
    for r in rows:
        if (REPORT_DIR / r["filename"]).exists():
            r["params"] = loads(r["params"], {})
            r["url"] = f"/files/reports/{r['filename']}"
            out.append(r)
    return out


@app.delete("/api/reports/{rid}")
def report_delete(rid: int):
    with db() as c:
        r = c.execute("SELECT filename FROM reports WHERE id = ?", (rid,)).fetchone()
        if r:
            (REPORT_DIR / r["filename"]).unlink(missing_ok=True)
            c.execute("DELETE FROM reports WHERE id = ?", (rid,))
    return {"ok": True}


@app.post("/api/export/transactions")
def export_transactions(b: dict = Body(default={})):
    where, args = [], []
    if b.get("start"):
        where.append(f"date >= '{b['start'][:10]}'")
    if b.get("end"):
        where.append(f"date <= '{b['end'][:10]}'")
    for v in (b.get("start"), b.get("end")):
        if v and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v[:10]):
            raise HTTPException(400, "bad date")
    sql = ("SELECT date, amount, currency, counterparty, description, category, reference, notes FROM transactions"
           + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY date")
    return reports.export_csv(sql, "transactions")


# ------------------------------------------------------------------ exchange rates

@app.get("/api/fx/status")
def fx_status():
    s = fx.status()
    with db() as c:
        s["missing"] = c.execute("SELECT COUNT(*) AS n FROM transactions WHERE fx_source = 'missing'").fetchone()["n"]
    s["base_currency"] = base_currency()
    return s


@app.post("/api/fx/refresh")
def fx_refresh():
    try:
        fx.ensure(force=True)
    except fx.FXError as e:
        raise HTTPException(503, str(e))
    return {"status": fx.status(), "updated": recompute()}


@app.get("/api/fx/convert")
def fx_convert(amount: float, frm: str, to: str = "", on: str = ""):
    try:
        return fx.convert(amount, frm, to or base_currency(), on or None)
    except fx.FXError as e:
        raise HTTPException(422, str(e))


# ------------------------------------------------------------------ assistant & skills

@app.post("/api/chat")
async def chat(b: dict = Body(...)):
    async def gen():
        async for ev in run_chat(b.get("messages") or []):
            yield json.dumps(ev, default=str) + "\n"
    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.get("/api/skills")
def skill_list():
    return [{k: s[k] for k in ("name", "description", "folder", "edited", "builtin")} for s in skills.list_skills()]


@app.get("/api/skills/{name}")
def skill_get(name: str):
    text = skills.read_skill(name)
    if not text:
        raise HTTPException(404)
    return {"name": name, "text": text}


@app.put("/api/skills/{name}")
def skill_put(name: str, b: dict = Body(...)):
    """Saved as your own copy in the data folder; the built-in skill stays as it is."""
    skills.write_skill(name, b["text"])
    return {"ok": True}


@app.delete("/api/skills/{name}")
def skill_reset(name: str):
    """Back to the built-in text."""
    return {"reset": skills.reset_skill(name), "text": skills.read_skill(name)}


@app.get("/api/prompt-preview")
def prompt_preview(tab: str = "assistant"):
    """The system prompt exactly as the model gets it for one kind of work (the task text of a tab is included)."""
    from .assistant import build_system
    from .documents import TASK
    from .importers import EXTRACT_TASK, MAPPING_TASK
    from .skills import owner_name, system_prompt
    me = owner_name() or "the owner of the books"
    if tab == "assistant":
        prompt = build_system()
    elif tab == "receipts":
        prompt = system_prompt("receipt-extraction", task=TASK.format(what="receipt", me=me))
    elif tab == "invoices":
        prompt = system_prompt("invoice-management", task=TASK.format(what="invoice", me=me))
    elif tab == "statements":
        prompt = system_prompt("bank-statement-import", task=EXTRACT_TASK, context=False)
    elif tab == "statement columns":
        prompt = system_prompt("bank-statement-import", task=MAPPING_TASK, context=False)
    elif tab == "categorize":
        prompt = system_prompt("transaction-categorization", task="(the numbered payees follow in the request)")
    elif tab == "email sorting":
        prompt = mail.CLASSIFY_PROMPT
    else:
        raise HTTPException(404)
    extra = len(json.dumps(tools.TOOL_DEFS)) if tab == "assistant" else 0
    return {"prompt": prompt, "chars": len(prompt), "tokens_estimate": (len(prompt) + extra) // 4,
            "note": "plus the tool definitions" if extra else ""}


@app.get("/api/skills.zip")
def skills_zip():
    """All SKILL.md files, to unpack into ~/.agents/skills on the computer that runs Unsloth Studio."""
    import io
    import zipfile
    from fastapi.responses import Response
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for sk in skills.list_skills():
            z.writestr(f"{sk['folder']}/SKILL.md", skills.read_skill(sk["folder"]))
        z.writestr("studio-project-instructions.md",
                   (BASE_DIR / "prompts" / "studio-project-instructions.md").read_text(encoding="utf-8").replace("{APP_URL}", tools.APP_URL))
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="ledger-skills.zip"'})


# ------------------------------------------------------------------ Unsloth Studio integration

def studio_url() -> str:
    """Unsloth Studio's own API = the model URL without /v1 (Studio may run on another machine)."""
    return re.sub(r"/v1/?$", "", get_settings()["llm_base_url"].rstrip("/"))


@app.post("/api/studio/install-skills")
def studio_install_skills():
    target = Path.home() / ".agents" / "skills"
    target.mkdir(parents=True, exist_ok=True)
    done = []
    for sk in skills.list_skills():
        dst = target / sk["folder"]
        dst.mkdir(exist_ok=True)
        (dst / "SKILL.md").write_text(skills.read_skill(sk["folder"]), encoding="utf-8")
        done.append(sk["folder"])
    studio_here = any(h in studio_url() for h in ("127.0.0.1", "localhost"))
    return {"installed": sorted(done), "folder": str(target), "same_machine": studio_here}


@app.post("/api/studio/register-mcp")
async def studio_register_mcp():
    url = tools.APP_URL + "/mcp"
    key = get_settings()["llm_api_key"].strip()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    async with httpx.AsyncClient(timeout=60, headers=headers) as c:
        STUDIO = studio_url()
        r = await c.get(f"{STUDIO}/api/mcp/servers/")
        if r.status_code == 401:
            raise HTTPException(401, "Unsloth Studio needs an API key: create one in Studio (Settings > API) and enter it "
                                     "under Settings > Local model > API key, then try again.")
        existing = r.json()
        found = next((s for s in existing if s.get("url") == url), None)
        old = next((s for s in existing if s.get("display_name") == "Bookkeeping Ledger"), None)
        if not found and old:  # registered earlier under another address (e.g. 127.0.0.1): move it
            r = await c.put(f"{STUDIO}/api/mcp/servers/{old['id']}", json={"url": url, "is_enabled": True})
            if r.status_code < 400:
                found = r.json()
        if not found:
            r = await c.post(f"{STUDIO}/api/mcp/servers/", json={"display_name": "Bookkeeping Ledger", "url": url, "is_enabled": True})
            if r.status_code >= 400:
                raise HTTPException(r.status_code, f"Studio refused: {r.text[:300]}. Add {url} manually in Studio > Settings > MCP.")
            found = r.json()
        # Studio only offers the tools after a refresh
        ref = (await c.post(f"{STUDIO}/api/mcp/servers/{found['id']}/refresh")).json()
        # project instructions written for an older address of this app: point them here
        moved = 0
        pr = await c.get(f"{STUDIO}/api/chat/projects")
        if pr.status_code == 200:
            for p in pr.json().get("projects", []):
                text = p.get("instructions") or ""
                new = re.sub(r"http://(127\.0\.0\.1|localhost):8890", tools.APP_URL, text)
                if new != text:
                    if (await c.patch(f"{STUDIO}/api/chat/projects/{p['id']}", json={"instructions": new})).status_code < 400:
                        moved += 1
    msg = f"'Bookkeeping Ledger' is registered in Unsloth Studio at {url} with {ref.get('tool_count')} tools."
    if moved:
        msg += f" Updated the app address in {moved} Studio project(s)."
    return {"ok": True, "url": url, "message": msg, "studio": found}
