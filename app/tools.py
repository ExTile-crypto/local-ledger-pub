"""Bookkeeping tools the model can call. Shared by the in-app Assistant and the MCP server (Unsloth Studio)."""
import json
import os

from . import reports
from .categorize import learn_rule
from .config import REPORT_DIR
from .db import categories, db, get_settings, now
from .importers import add_fingerprints, detect_original, parse_date
from .ledger import base_currency, doc_fx, tx_fx
from . import cash, fx
from .reconcile import auto_match, missing_documents as _missing, suggestions

APP_URL = os.environ.get("LEDGER_URL", "http://127.0.0.1:8890")

LEDGER_SCHEMA_DOC = """Ledger database (SQLite; run_sql is read-only). amount < 0 = money out, amount > 0 = money in.
Every money table also stores the value in the base currency (ECB rate of that date): use amount_base / total_base /
vat_base / net_base for any total, so currencies are never mixed. `currency` is the original currency.
- transactions(id, date 'YYYY-MM-DD', amount, currency, amount_base, fx_rate, fx_source 'same'|'ecb'|'manual'|'missing', orig_amount, orig_currency (foreign amount of a card payment abroad), description, counterparty, reference, category, category_source 'rule'|'keyword'|'history'|'llm'|'user'|'receipt'|'invoice'|'manual'|'', account_id -> accounts.id, import_id, balance_after, notes)
- categories(name, kind 'income'|'expense'|'transfer'|'other', description, keywords)  -- JOIN categories k ON k.name = t.category; income and spending are kind IN ('income','expense'); transfers are neither; 'other' = Uncategorized
- accounts(id, name, iban, currency, kind 'bank'|'cash')  -- the cash book is the account with kind='cash'
- account_cards(account_id, last4, label)  -- payment cards per account; receipts.card_last4 = the card printed on a receipt
- receipts(id, date, merchant, merchant_tax_id, total, net, vat, currency, total_base, net_base, vat_base, fx_rate, payment_method 'card'|'cash'|'transfer'|'mobile'|'other'|'unknown', card_last4, category, items JSON, vat_lines JSON, transaction_id -> transactions.id or NULL (not matched), status 'review'|'confirmed', confidence, notes, original_name)
- invoices(id, direction 'sales'|'purchase', number, counterparty, counterparty_tax_id, issue_date, due_date, net, vat, total, currency, total_base, net_base, vat_base, fx_rate, category, status 'unpaid'|'paid', transaction_id, notes)
- rules(pattern, category)  -- learned: text contained in counterparty/description -> category
Handy: month = substr(date,1,7); year = substr(date,1,4); spending as a positive number = -SUM(amount_base) over
kind='expense'; a transaction is documented when a receipt or invoice has transaction_id = its id; JSON columns are
read with json_each(receipts.items). The settings and email tables cannot be read."""


def _valid_category(name: str | None) -> tuple[str, str | None]:
    """(category to store, warning or None) - an unknown name never reaches the books."""
    names = {c["name"] for c in categories()}
    if not name or name in names:
        return name or "Uncategorized", None
    low = {n.lower(): n for n in names}
    if name.strip().lower() in low:
        return low[name.strip().lower()], None
    return "Uncategorized", f"Unknown category '{name}' - stored as Uncategorized. Valid: {sorted(names)}"


def _file(info: dict) -> dict:
    info["download_url"] = APP_URL + info["url"]
    info["path"] = str(REPORT_DIR / info["filename"])
    return info


def _range(q: str, args: list, start, end, col="t.date"):
    if start:
        q += f" AND {col} >= ?"
        args.append(start)
    if end:
        q += f" AND {col} <= ?"
        args.append(end)
    return q


def ledger_overview(start_date: str = None, end_date: str = None) -> dict:
    cur = base_currency()
    with db(readonly=True) as c:
        base = ("FROM transactions t LEFT JOIN categories k ON k.name = t.category WHERE t.amount_base IS NOT NULL")
        args = []
        base = _range(base, args, start_date, end_date)
        tot = c.execute(
            "SELECT COALESCE(SUM(CASE WHEN k.kind='income' THEN amount_base END),0) AS income, "
            "COALESCE(-SUM(CASE WHEN k.kind='expense' THEN amount_base END),0) AS expenses, "
            "COUNT(*) AS transactions, COALESCE(SUM(CASE WHEN COALESCE(k.kind,'other')='other' THEN 1 ELSE 0 END),0) AS uncategorized, "
            "MIN(t.date) AS first_date, MAX(t.date) AS last_date " + base, args).fetchone()
        months = c.execute(
            "SELECT substr(t.date,1,7) AS month, ROUND(COALESCE(SUM(CASE WHEN k.kind='income' THEN amount_base END),0),2) AS income, "
            "ROUND(COALESCE(-SUM(CASE WHEN k.kind='expense' THEN amount_base END),0),2) AS expenses " + base +
            " GROUP BY month ORDER BY month", args).fetchall()
        cats = c.execute(
            "SELECT t.category, k.kind, ROUND(SUM(amount_base),2) AS total, COUNT(*) AS n " + base +
            " GROUP BY t.category ORDER BY abs(SUM(amount_base)) DESC LIMIT 25", args).fetchall()
        other_cur = c.execute("SELECT currency, COUNT(*) AS n, ROUND(SUM(amount),2) AS net, ROUND(SUM(amount_base),2) AS net_base "
                              "FROM transactions WHERE currency != ? GROUP BY currency", (cur,)).fetchall()
        no_rate = c.execute("SELECT COUNT(*) AS n FROM transactions WHERE amount_base IS NULL").fetchone()["n"]
        miss = len(_missing(c, start_date, end_date))
        open_inv = c.execute("SELECT direction, COUNT(*) AS n, ROUND(SUM(total_base),2) AS total FROM invoices "
                             "WHERE status='unpaid' GROUP BY direction").fetchall()
        unmatched_receipts = c.execute("SELECT COUNT(*) AS n FROM receipts WHERE transaction_id IS NULL").fetchone()["n"]
    tot["income"], tot["expenses"] = round(tot["income"], 2), round(tot["expenses"], 2)
    tot["net"] = round(tot["income"] - tot["expenses"], 2)
    return {"currency": cur, "period": {"start": start_date or tot["first_date"], "end": end_date or tot["last_date"]},
            "totals": tot, "by_month": months, "by_category": cats, "converted_currencies": other_cur,
            "transactions_without_exchange_rate": no_rate,
            "expenses_without_documents": miss, "open_invoices": open_inv, "receipts_not_matched_to_bank": unmatched_receipts}


def run_sql(query: str) -> dict:
    cols, rows = reports.run_select(query, limit=2000)
    shown = rows[:200]
    return {"columns": cols, "rows": shown, "row_count": len(rows),
            "note": "showing first 200 rows" if len(rows) > 200 else ""}


def search_transactions(text: str = None, start_date: str = None, end_date: str = None, category: str = None,
                        min_amount: float = None, max_amount: float = None, uncategorized_only: bool = False,
                        limit: int = 50, account: str = None) -> dict:
    q = "SELECT t.id, t.date, t.amount, t.currency, t.amount_base, t.counterparty, t.description, t.category FROM transactions t WHERE 1=1"
    args = []
    if account:
        q += " AND t.account_id IN (SELECT id FROM accounts WHERE name LIKE ?)"
        args.append(f"%{account}%")
    if text:
        q += " AND (t.description LIKE ? OR t.counterparty LIKE ? OR t.reference LIKE ?)"
        args += [f"%{text}%"] * 3
    q = _range(q, args, start_date, end_date)
    if category:
        q += " AND t.category = ?"
        args.append(category)
    if min_amount is not None:
        q += " AND t.amount >= ?"
        args.append(min_amount)
    if max_amount is not None:
        q += " AND t.amount <= ?"
        args.append(max_amount)
    if uncategorized_only:
        q += " AND t.category = 'Uncategorized'"
    with db(readonly=True) as c:
        n = c.execute(q.replace("SELECT t.id, t.date, t.amount, t.currency, t.amount_base, t.counterparty, t.description, t.category",
                                "SELECT COUNT(*) AS n", 1), args).fetchone()["n"]
        rows = c.execute(q + " ORDER BY t.date DESC LIMIT ?", args + [min(int(limit or 50), 300)]).fetchall()
    return {"match_count": n, "transactions": rows}


def list_uncategorized(limit: int = 30) -> dict:
    with db(readonly=True) as c:
        total = c.execute("SELECT COUNT(*) AS n FROM transactions WHERE category = 'Uncategorized'").fetchone()["n"]
        groups = c.execute(
            "SELECT COALESCE(NULLIF(counterparty, ''), description) AS payee, CASE WHEN amount < 0 THEN 'out' ELSE 'in' END AS direction, "
            "COUNT(*) AS payments, ROUND(SUM(amount_base), 2) AS total_base, MIN(date) AS first_date, MAX(date) AS last_date, "
            "MAX(description) AS example_text FROM transactions WHERE category = 'Uncategorized' "
            "GROUP BY 1, 2 ORDER BY payments DESC, abs(SUM(amount_base)) DESC LIMIT ?", (min(int(limit or 30), 100),)).fetchall()
        n_groups = c.execute("SELECT COUNT(*) AS n FROM (SELECT 1 FROM transactions WHERE category = 'Uncategorized' "
                             "GROUP BY COALESCE(NULLIF(counterparty, ''), description), amount < 0)").fetchone()["n"]
    for g in groups:
        if g["example_text"] == g["payee"]:
            g.pop("example_text")
    return {"uncategorized_transactions": total, "payees": n_groups, "groups": groups,
            "note": "set_category(counterparty=<payee>, category=..., remember=true) categorizes a whole group at once"}


def set_category(transaction_ids: list = None, category: str = None, remember: bool = False, counterparty: str = None,
                 only_uncategorized: bool = False) -> dict:
    names = {c["name"] for c in categories()}
    if category not in names:
        return {"error": f"Unknown category '{category}'. Valid: {sorted(names)}"}
    ids = [int(i) for i in transaction_ids or []]
    text = (counterparty or "").strip()
    if not ids and len(text) < 3:
        return {"error": "Give transaction_ids, or counterparty (at least 3 letters) to change every line of that payee."}
    with db() as c:
        if not ids:
            q = "SELECT id FROM transactions WHERE (counterparty LIKE ? OR description LIKE ?)"
            if only_uncategorized:
                q += " AND category = 'Uncategorized'"
            ids = [r["id"] for r in c.execute(q, (f"%{text}%", f"%{text}%"))]
        rows = []
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            c.execute(f"UPDATE transactions SET category = ?, category_source = 'user' WHERE id IN ({marks})", [category, *chunk])
            rows += c.execute(f"SELECT id, date, amount, counterparty, description FROM transactions WHERE id IN ({marks})", chunk).fetchall()
    learned = []
    if remember:
        for r in ([{"counterparty": text, "description": ""}] if text else rows[:5]):
            p = learn_rule(r["counterparty"], r["description"], category)
            if p and p not in learned:
                learned.append(p)
    return {"updated": len(rows), "category": category, "rules_learned": learned,
            "examples": [{k: r[k] for k in ("id", "date", "amount", "counterparty")} for r in rows[:5]]}


def auto_categorize(scope: str = "uncategorized") -> dict:
    """The ledger's own pipeline (rules, keywords, earlier choices, then the model per payee) - called from a worker thread."""
    import asyncio
    from .categorize import recategorize
    return asyncio.run(recategorize("weak" if scope == "weak" else "uncategorized"))


def add_transactions(transactions: list, account_name: str = None) -> dict:
    s = get_settings()
    rows, notes = [], []
    for t in transactions:
        d = parse_date(str(t.get("date", "")))
        if not d or t.get("amount") is None:
            continue
        cat, warn = _valid_category(t.get("category"))
        if warn and warn not in notes:
            notes.append(warn)
        rows.append({"date": d, "amount": round(float(t["amount"]), 2),
                     "currency": (t.get("currency") or s["base_currency"]).upper()[:3],
                     "description": t.get("description", ""), "counterparty": t.get("counterparty", ""),
                     "reference": t.get("reference", ""), "category": cat,
                     "notes": t.get("notes", "")})
        detect_original(rows[-1])
        tx_fx(rows[-1])
    with db() as c:
        acc_id = None
        cash_acc = c.execute("SELECT name FROM accounts WHERE kind = 'cash' LIMIT 1").fetchone()
        wanted = (account_name or "").strip().lower()
        if wanted and (wanted in cash.CASH_NAMES or (cash_acc and wanted == cash_acc["name"].lower())):
            acc_id = cash.account_id(c)
        elif account_name:
            a = c.execute("SELECT id FROM accounts WHERE name = ?", (account_name,)).fetchone()
            acc_id = a["id"] if a else c.execute("INSERT INTO accounts(name, created_at) VALUES (?,?)",
                                                 (account_name, now())).lastrowid
        add_fingerprints(rows, acc_id)
        inserted = 0
        for r in rows:
            cur = c.execute(
                "INSERT OR IGNORE INTO transactions(date, amount, currency, description, counterparty, reference, category, "
                "category_source, account_id, notes, fingerprint, created_at, orig_amount, orig_currency, fx_rate, fx_source, "
                "amount_base) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r["date"], r["amount"], r["currency"], r["description"], r["counterparty"], r["reference"], r["category"],
                 "manual", acc_id, r["notes"], r["fingerprint"], now(), r.get("orig_amount"), r.get("orig_currency") or "",
                 r["fx_rate"], r["fx_source"], r["amount_base"]))
            inserted += cur.rowcount
    out = {"inserted": inserted, "skipped_duplicates_or_invalid": len(transactions) - inserted}
    if notes:
        out["warnings"] = notes
    return out


def save_receipt(date: str, merchant: str, total: float, currency: str = None, net: float = None, vat: float = None,
                 category: str = "Uncategorized", payment_method: str = "unknown", vat_lines: list = None,
                 items: list = None, notes: str = "", card_last4: str = None, merchant_tax_id: str = None,
                 allow_duplicate: bool = False) -> dict:
    from .cards import norm as card_norm
    from .mail import _duplicate
    category, warn = _valid_category(category)
    if payment_method not in ("card", "cash", "transfer", "mobile", "other", "unknown"):
        payment_method = "unknown"
    d = doc_fx({"date": parse_date(date) or date, "total": total, "net": net, "vat": vat,
                "currency": (currency or base_currency()).upper()}, "date")
    l4 = card_norm(card_last4)
    with db() as c:
        dup = None if allow_duplicate else _duplicate(c, {"total": total, "date": d["date"], "currency": d["currency"],
                                                         "merchant": merchant})
        if dup:
            return {"saved": False, "duplicate": dup,
                    "note": "Not saved again. If it really is a second purchase, call save_receipt with allow_duplicate=true."}
        rid = c.execute(
            "INSERT INTO receipts(date, merchant, merchant_tax_id, total, net, vat, currency, payment_method, category, items, "
            "vat_lines, status, notes, created_at, fx_rate, fx_source, total_base, net_base, vat_base, card_last4) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (d["date"], merchant, merchant_tax_id or "", total, net, vat, d["currency"],
             "card" if l4 and payment_method in ("cash", "unknown") else payment_method, category, json.dumps(items or []),
             json.dumps(vat_lines or []), "confirmed", notes, now(), d["fx_rate"], d["fx_source"], d["total_base"],
             d["net_base"], d["vat_base"], l4)).lastrowid
        booked = cash.sync_receipt(c, rid)
    linked = auto_match()
    with db(readonly=True) as c:
        tx = c.execute("SELECT t.id, t.date, t.amount, t.description FROM receipts r JOIN transactions t ON t.id = r.transaction_id "
                       "WHERE r.id = ?", (rid,)).fetchone()
    out = {"saved": True, "receipt_id": rid, "total_base": d["total_base"], "base_currency": base_currency(),
           "matched_transaction": None if booked else tx, "auto_matched_documents": linked,
           "cash_book": "booked as a cash expense in the cash book" if booked else None}
    if warn:
        out["warning"] = warn
    return out


def save_invoice(direction: str, counterparty: str, issue_date: str, total: float, number: str = "",
                 due_date: str = None, net: float = None, vat: float = None, currency: str = None,
                 category: str = "Uncategorized", status: str = "unpaid", notes: str = "") -> dict:
    if direction not in ("sales", "purchase"):
        return {"error": "direction must be 'sales' (the owner gets paid) or 'purchase' (the owner pays)"}
    category, warn = _valid_category(category)
    d = doc_fx({"issue_date": parse_date(issue_date) or issue_date, "total": total, "net": net, "vat": vat,
                "currency": (currency or base_currency()).upper()}, "issue_date")
    with db() as c:
        if number and c.execute("SELECT 1 FROM invoices WHERE number = ? AND counterparty = ? AND abs(total - ?) < 0.011",
                                (number, counterparty, total)).fetchone():
            return {"saved": False, "duplicate": f"invoice {number} from {counterparty} is already saved"}
        iid = c.execute(
            "INSERT INTO invoices(direction, number, counterparty, issue_date, due_date, net, vat, total, currency, category, "
            "status, notes, created_at, fx_rate, fx_source, total_base, net_base, vat_base) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (direction, number, counterparty, d["issue_date"], parse_date(due_date or "") or None,
             net, vat, total, d["currency"], category, "paid" if status == "paid" else "unpaid", notes, now(), d["fx_rate"],
             d["fx_source"], d["total_base"], d["net_base"], d["vat_base"])).lastrowid
    linked = auto_match()
    out = {"saved": True, "invoice_id": iid, "total_base": d["total_base"], "auto_matched_documents": linked}
    if warn:
        out["warning"] = warn
    return out


def missing_documents(start_date: str = None, end_date: str = None, min_amount: float = 0) -> dict:
    with db(readonly=True) as c:
        rows = _missing(c, start_date, end_date, float(min_amount or 0))
    return {"count": len(rows), "total_base_currency": round(-sum(r["amount_base"] or 0 for r in rows), 2),
            "transactions": [{k: r[k] for k in ("id", "date", "amount", "currency", "amount_base", "counterparty", "description", "category")}
                             for r in rows[:100]]}


def reconcile() -> dict:
    linked = auto_match()
    with db(readonly=True) as c:
        sug = suggestions(c)
    return {"newly_linked": linked, "open_suggestions": [
        {"doc_type": s["doc_type"], "doc_id": s["doc"]["id"], "transaction_id": s["transaction"]["id"],
         "amount": s["transaction"]["amount"], "date": s["transaction"]["date"], "score": s["score"], "reason": s["reason"]}
        for s in sug[:30]]}


def link_document(doc_type: str, doc_id: int, transaction_id: int = None) -> dict:
    from .reconcile import link
    if doc_type not in ("receipt", "invoice"):
        return {"error": "doc_type must be 'receipt' or 'invoice'"}
    table = "receipts" if doc_type == "receipt" else "invoices"
    with db() as c:
        if not c.execute(f"SELECT 1 FROM {table} WHERE id = ?", (int(doc_id),)).fetchone():
            return {"error": f"No {doc_type} with id {doc_id}"}
        if transaction_id is not None:
            if not c.execute("SELECT 1 FROM transactions WHERE id = ?", (int(transaction_id),)).fetchone():
                return {"error": f"No transaction with id {transaction_id}"}
            other = c.execute("SELECT 'receipt' AS t, id FROM receipts WHERE transaction_id = ? UNION ALL "
                              "SELECT 'invoice', id FROM invoices WHERE transaction_id = ?",
                              (int(transaction_id), int(transaction_id))).fetchone()
            if other and (other["t"], other["id"]) != (doc_type, int(doc_id)):
                return {"error": f"Transaction {transaction_id} already belongs to {other['t']} #{other['id']}"}
        link(c, doc_type, int(doc_id), int(transaction_id) if transaction_id is not None else None)
    return {"linked": transaction_id is not None, "doc_type": doc_type, "doc_id": int(doc_id), "transaction_id": transaction_id}


def convert_currency(amount: float, from_currency: str, to_currency: str = None, date: str = None) -> dict:
    return fx.convert(float(amount), from_currency, to_currency or base_currency(), date)


def list_categories() -> dict:
    return {"categories": [{k: c[k] for k in ("name", "kind", "description")} for c in categories()]}


def create_overview_workbook(start_date: str = None, end_date: str = None, title: str = None) -> dict:
    return _file(reports.build_overview(start_date, end_date, title))


def create_custom_workbook(title: str, sheets: list, notes: str = "") -> dict:
    return _file(reports.build_custom(title, sheets, notes))


def export_csv(query: str, title: str) -> dict:
    return _file(reports.export_csv(query, title))


# ---------------------------------------------------------------- tool definitions (Assistant + MCP)

def _fn(name, desc, props, required=()):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": list(required)}}}


S, N, B, I = {"type": "string"}, {"type": "number"}, {"type": "boolean"}, {"type": "integer"}
DATE = {"type": "string", "description": "YYYY-MM-DD"}
PAYMENT = {"type": "string", "enum": ["card", "cash", "transfer", "mobile", "other", "unknown"]}

# One definition per tool, used by the in-app Assistant as-is and by the MCP server for its descriptions.
# In the app the database schema is in the system prompt; MCP clients get it in run_sql's description (mcp_server.py).
TOOL_DEFS = [
    _fn("ledger_overview", "Income, spending and net for a period, per month and per category, plus open invoices and "
        "expenses without a document. Start here for 'how much / how did the month go' questions. No dates = everything.",
        {"start_date": DATE, "end_date": DATE}),
    _fn("run_sql", "Run one read-only SQLite SELECT against the ledger, for any calculation or list the other tools do "
        "not cover. Returns at most 200 rows - aggregate (GROUP BY, SUM) instead of fetching lines to add up yourself.",
        {"query": S}, ["query"]),
    _fn("search_transactions", "Find transactions by text (counterparty, description, reference), date range, category, "
        "account name or amount range. Amounts are signed: money out is negative, so 'over 1000 spent' is max_amount=-1000.",
        {"text": S, "start_date": DATE, "end_date": DATE, "category": S, "account": S, "min_amount": N, "max_amount": N,
         "uncategorized_only": B, "limit": I}),
    _fn("list_uncategorized", "Uncategorized transactions grouped by payee, biggest groups first. Use it to review what "
        "still needs a category.", {"limit": I}),
    _fn("set_category", "Change the category of transactions: either transaction_ids, or counterparty = every line whose "
        "counterparty or text contains it (only_uncategorized=true leaves already categorized lines alone). "
        "remember=true also saves a rule, so future lines of that payee get the category automatically.",
        {"category": S, "transaction_ids": {"type": "array", "items": I}, "counterparty": S, "only_uncategorized": B,
         "remember": B}, ["category"]),
    _fn("auto_categorize", "Run the ledger's automatic categorization over all uncategorized transactions (learned rules, "
        "keywords, earlier choices, then the model once per payee). scope 'weak' also re-checks lines the model earlier "
        "put in Other expenses / Other income. Takes a minute or two for many lines; returns counts per category.",
        {"scope": {"type": "string", "enum": ["uncategorized", "weak"]}}),
    _fn("add_transactions", "Add transactions the user pasted or dictated. amount < 0 = money out. account_name is an "
        "existing account (or a new one); 'cash' = the cash book (cash paid or received without a receipt). "
        "Duplicates of lines already in that account are skipped.",
        {"transactions": {"type": "array", "items": {"type": "object", "properties": {
            "date": DATE, "amount": N, "description": S, "counterparty": S, "currency": S, "category": S, "notes": S},
            "required": ["date", "amount", "description"]}}, "account_name": S}, ["transactions"]),
    _fn("save_receipt", "Store a receipt you have read (for example from an image in the chat), in its ORIGINAL currency. "
        "Paid in cash -> booked in the cash book; otherwise it is matched to its bank transaction. A receipt that is "
        "already saved is reported as a duplicate instead of being saved twice.",
        {"date": DATE, "merchant": S, "total": N, "currency": S, "net": N, "vat": N, "category": S,
         "payment_method": PAYMENT, "card_last4": {"type": "string", "description": "last 4 card digits printed on the receipt"},
         "merchant_tax_id": S,
         "vat_lines": {"type": "array", "items": {"type": "object", "properties": {"rate": N, "net": N, "vat": N, "gross": N}}},
         "items": {"type": "array", "items": {"type": "object", "properties": {"description": S, "quantity": N, "total": N}}},
         "notes": S, "allow_duplicate": B}, ["date", "merchant", "total"]),
    _fn("save_invoice", "Store an invoice: direction 'purchase' = the owner pays (the normal case), 'sales' = the owner "
        "gets paid. It is marked paid when its bank payment is found.",
        {"direction": {"type": "string", "enum": ["sales", "purchase"]}, "counterparty": S, "issue_date": DATE, "total": N,
         "number": S, "due_date": DATE, "net": N, "vat": N, "currency": S, "category": S,
         "status": {"type": "string", "enum": ["unpaid", "paid"]}, "notes": S},
        ["direction", "counterparty", "issue_date", "total"]),
    _fn("missing_documents", "Money-out transactions with no receipt or invoice attached (transfers, taxes and bank fees "
        "excluded), newest first. min_amount is a positive number: only payments of at least that size.",
        {"start_date": DATE, "end_date": DATE, "min_amount": N}),
    _fn("reconcile", "Link receipts and invoices to their bank transactions where the match is clear, and list the "
        "remaining suggestions with a score and reason.", {}),
    _fn("link_document", "Link one receipt or invoice to a bank transaction after the user agreed (for example a "
        "suggestion from reconcile). Leave transaction_id out to unlink.",
        {"doc_type": {"type": "string", "enum": ["receipt", "invoice"]}, "doc_id": I, "transaction_id": I},
        ["doc_type", "doc_id"]),
    _fn("convert_currency", "Convert an amount between currencies with the ECB reference rate of a date "
        "(default: to the base currency, at the latest rate). Returns the rate and its date.",
        {"amount": N, "from_currency": S, "to_currency": S, "date": DATE}, ["amount", "from_currency"]),
    _fn("create_overview_workbook", "Create the standard Excel overview for a period: summary, monthly, categories, "
        "category x month, transactions, receipts, invoices, VAT, missing documents - with charts and live formulas. "
        "Returns the download link.",
        {"start_date": DATE, "end_date": DATE, "title": S}),
    _fn("create_custom_workbook", "Create a custom Excel workbook: one sheet per SQL SELECT, with an optional totals row "
        "and chart. For analyses the standard overview does not cover. Returns the download link.",
        {"title": S, "notes": {"type": "string", "description": "Shown on the About sheet: period, currency, what is excluded"},
         "sheets": {"type": "array", "items": {"type": "object", "properties": {
             "name": S, "sql": S, "totals": B,
             "chart": {"type": "object", "properties": {
                 "type": {"type": "string", "enum": ["none", "column", "bar", "line", "pie"]},
                 "category_column": S, "value_columns": {"type": "array", "items": S}, "title": S}}},
             "required": ["name", "sql"]}}}, ["title", "sheets"]),
    _fn("export_csv", "Export the result of a SELECT to a CSV file (for an accountant or other software). "
        "Returns the download link.", {"query": S, "title": S}, ["query", "title"]),
]

REGISTRY = {
    "ledger_overview": ledger_overview, "run_sql": run_sql, "search_transactions": search_transactions,
    "list_uncategorized": list_uncategorized, "set_category": set_category, "auto_categorize": auto_categorize,
    "add_transactions": add_transactions, "save_receipt": save_receipt, "save_invoice": save_invoice,
    "missing_documents": missing_documents, "reconcile": reconcile, "link_document": link_document,
    "list_categories": list_categories, "convert_currency": convert_currency,
    "create_overview_workbook": create_overview_workbook, "create_custom_workbook": create_custom_workbook,
    "export_csv": export_csv,
}


def tool_description(name: str) -> str:
    return next(t["function"]["description"] for t in TOOL_DEFS if t["function"]["name"] == name)


def call_tool(name: str, args: dict) -> dict:
    """Run a tool for the model. Errors are returned as data, so the model can read them and correct its call."""
    fn = REGISTRY.get(name)
    if not fn:
        return {"error": f"Unknown tool {name}. Tools: {', '.join(REGISTRY)}"}
    args = {k: v for k, v in (args or {}).items() if v is not None}   # models often send null for 'not given'
    try:
        return fn(**args)
    except TypeError as e:
        spec = next((t["function"]["parameters"] for t in TOOL_DEFS if t["function"]["name"] == name), {})
        return {"error": f"Bad arguments for {name}: {e}. Accepted: {', '.join(spec.get('properties', {})) or 'none'}; "
                         f"required: {', '.join(spec.get('required', [])) or 'none'}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
