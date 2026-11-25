"""Cash book: one account of kind 'cash' (named by the country pack: Kontanter, Gotówka, Cash) for money paid and received in cash.

- A receipt paid in cash is booked automatically as a cash expense, linked to the receipt (so it counts in the
  results, the moms and is 'documented'). Changing the payment method away from cash removes that entry again.
- Cash in: ATM withdrawals from the bank (a transfer: bank -> cash box, so it is not income) or cash sales.
- Cash out without a receipt: small cash expenses (they show up under missing documents).
"""
import re
import uuid

from .db import now
from .importers import parse_date
from .ledger import base_currency, tx_fx

TRANSFER = "Transfer between own accounts"
CASH_NAMES = ("cash", "kontanter", "kontant", "kasse", "gotówka", "gotowka", "bargeld", "kassa")   # accepted from tools and chats
# bank texts that mean "cash taken out of the account" (not "privat hævning", which is an owner withdrawal)
ATM = re.compile(r"\b(kontanthævning|hævning i automat|pengeautomat|geldautomat|hæveautomat|atm|cash withdrawal|"
                 r"udbetaling kontant|kontant udbetaling|wypłata z bankomatu|wyplata z bankomatu|wypłata gotówki|bankomat|"
                 r"bargeldauszahlung|uttag)\b|^hævning\b", re.I)


def account_id(c) -> int:
    row = c.execute("SELECT id FROM accounts WHERE kind = 'cash' ORDER BY id LIMIT 1").fetchone()
    if row:
        return row["id"]
    from .packs import pack
    return c.execute("INSERT INTO accounts(name, iban, currency, kind, created_at) VALUES (?, '', ?, 'cash', ?)",
                     (pack()["cash_account"], base_currency(), now())).lastrowid


def _insert(c, fp: str, row: dict) -> int:
    tx_fx(row)
    return c.execute(
        "INSERT INTO transactions(date, amount, currency, description, counterparty, reference, category, category_source, "
        "account_id, notes, fingerprint, created_at, fx_rate, fx_source, amount_base) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (row["date"], row["amount"], row["currency"], row.get("description", ""), row.get("counterparty", ""),
         row.get("reference", ""), row.get("category") or "Uncategorized", row.get("category_source", "manual"),
         account_id(c), row.get("notes", ""), fp, now(), row["fx_rate"], row["fx_source"], row["amount_base"])).lastrowid


def sync_receipt(c, rid: int) -> str | None:
    """Keep a receipt's cash-book entry in line with the receipt. Returns 'booked', 'updated', 'removed' or None."""
    r = c.execute("SELECT * FROM receipts WHERE id = ?", (rid,)).fetchone()
    fp = f"cash-receipt-{rid}"
    entry = c.execute("SELECT * FROM transactions WHERE fingerprint = ?", (fp,)).fetchone()
    if not r:
        if entry:
            c.execute("DELETE FROM transactions WHERE id = ?", (entry["id"],))
            return "removed"
        return None
    is_cash = (r["payment_method"] or "").lower() == "cash" and r["total"] is not None and parse_date(r["date"] or "")
    if not is_cash:
        if entry:
            c.execute("UPDATE receipts SET transaction_id = NULL WHERE id = ? AND transaction_id = ?", (rid, entry["id"]))
            c.execute("DELETE FROM transactions WHERE id = ?", (entry["id"],))
            return "removed"
        return None
    merchant = r["merchant"] or "Cash purchase"
    row = {"date": parse_date(r["date"]), "amount": -abs(round(r["total"], 2)), "currency": (r["currency"] or base_currency()).upper(),
           "description": f"Cash: {merchant}", "counterparty": merchant, "category": r["category"] or "Uncategorized",
           "category_source": "receipt", "reference": f"receipt #{rid}"}
    if entry:
        tx_fx(row)
        c.execute("UPDATE transactions SET date=?, amount=?, currency=?, description=?, counterparty=?, fx_rate=?, fx_source=?, "
                  "amount_base=?, category = CASE WHEN category_source = 'user' THEN category ELSE ? END WHERE id = ?",
                  (row["date"], row["amount"], row["currency"], row["description"], row["counterparty"], row["fx_rate"],
                   row["fx_source"], row["amount_base"], row["category"], entry["id"]))
        tid, result = entry["id"], "updated"
    else:
        tid, result = _insert(c, fp, row), "booked"
    # a cash receipt belongs to its cash entry, never to a bank line
    c.execute("UPDATE receipts SET transaction_id = ? WHERE id = ?", (tid, rid))
    return result


def record(c, date: str, amount: float, description: str, category: str, currency: str | None = None,
           counterparty: str = "", source_tx_id: int | None = None, notes: str = "") -> int:
    """Add a cash movement: amount > 0 = cash in, < 0 = cash out. source_tx_id = the bank withdrawal it came from."""
    d = parse_date(date)
    if not d:
        raise ValueError("Invalid date")
    if source_tx_id:
        fp = f"cash-in-{source_tx_id}"
        if c.execute("SELECT 1 FROM transactions WHERE fingerprint = ?", (fp,)).fetchone():
            raise ValueError("This bank withdrawal is already in the cash book")
        # the bank side of the withdrawal is a transfer too, unless the user chose something else
        c.execute("UPDATE transactions SET category = ?, category_source = 'cash' WHERE id = ? AND category_source != 'user'",
                  (TRANSFER, source_tx_id))
    else:
        fp = f"cash-manual-{uuid.uuid4().hex[:12]}"
    return _insert(c, fp, {"date": d, "amount": round(float(amount), 2), "currency": (currency or base_currency()).upper(),
                           "description": description, "counterparty": counterparty, "category": category,
                           "reference": f"bank tx #{source_tx_id}" if source_tx_id else "", "notes": notes})


def withdrawals(c) -> list[dict]:
    """Bank lines that look like cash withdrawals and are not yet in the cash book."""
    rows = c.execute(
        "SELECT t.*, a.name AS account FROM transactions t LEFT JOIN accounts a ON a.id = t.account_id "
        "WHERE t.amount < 0 AND COALESCE(a.kind, 'bank') != 'cash' "
        "AND NOT EXISTS (SELECT 1 FROM transactions x WHERE x.fingerprint = 'cash-in-' || t.id) "
        "ORDER BY t.date DESC LIMIT 500").fetchall()
    return [t for t in rows if ATM.search(f"{t['description']} {t['counterparty']}")
            and "privat" not in (t["description"] or "").lower()]


def book(c, start: str = "", end: str = "") -> dict:
    """Cash book rows with a running balance per currency (balance carried in from before the period)."""
    acc = c.execute("SELECT * FROM accounts WHERE kind = 'cash' ORDER BY id LIMIT 1").fetchone()
    if not acc:
        return {"account": None, "rows": [], "balances": {}, "opening": {}, "withdrawals": withdrawals(c)}
    opening = {r["currency"]: round(r["s"], 2) for r in c.execute(
        "SELECT currency, SUM(amount) AS s FROM transactions WHERE account_id = ? AND date < ? GROUP BY currency",
        (acc["id"], start or "0000"))}
    q = ("SELECT t.*, (SELECT r.id FROM receipts r WHERE r.transaction_id = t.id LIMIT 1) AS receipt_id "
         "FROM transactions t WHERE t.account_id = ?")
    args = [acc["id"]]
    if start:
        q += " AND t.date >= ?"
        args.append(start)
    if end:
        q += " AND t.date <= ?"
        args.append(end)
    rows = c.execute(q + " ORDER BY t.date, t.id", args).fetchall()
    bal = dict(opening)
    for t in rows:
        bal[t["currency"]] = round(bal.get(t["currency"], 0) + t["amount"], 2)
        t["balance"] = bal[t["currency"]]
    total = {r["currency"]: round(r["s"], 2) for r in c.execute(
        "SELECT currency, SUM(amount) AS s FROM transactions WHERE account_id = ? GROUP BY currency", (acc["id"],))}
    return {"account": acc, "rows": rows, "opening": opening, "balances": total,
            "cash_in": round(sum(t["amount"] for t in rows if t["amount"] > 0), 2),
            "cash_out": round(-sum(t["amount"] for t in rows if t["amount"] < 0), 2),
            "withdrawals": withdrawals(c)}
