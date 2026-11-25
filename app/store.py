"""Saving documents the model has read (uploads and emails share this)."""
import json
from pathlib import Path

from . import cards, cash
from .config import UPLOAD_DIR
from .db import db, now
from .ledger import doc_fx


def rel(p: Path) -> str:
    return str(Path(p).relative_to(UPLOAD_DIR))


def doc_fx_update(c, table: str, doc_id: int, manual_rate=None):
    dk = "date" if table == "receipts" else "issue_date"
    r = c.execute(f"SELECT id, {dk}, total, net, vat, currency, fx_rate, fx_source FROM {table} WHERE id = ?", (doc_id,)).fetchone()
    if not r:
        return
    if manual_rate is not None:
        r["fx_rate"], r["fx_source"] = (float(manual_rate), "manual") if manual_rate else (None, "")
    elif r["fx_source"] != "manual":
        r["fx_source"] = ""
    doc_fx(r, dk)
    c.execute(f"UPDATE {table} SET fx_rate=?, fx_source=?, total_base=?, net_base=?, vat_base=? WHERE id=?",
              (r["fx_rate"], r["fx_source"], r["total_base"], r["net_base"], r["vat_base"], doc_id))


def save_receipt(path: Path, original_name: str, d: dict, notes_prefix: str = "") -> tuple[int, str | None]:
    """Insert an extracted receipt. Returns (receipt id, cash-book result)."""
    notes = (notes_prefix + " " + (d.get("notes") or "")).strip()
    with db() as c:
        rid = c.execute(
            "INSERT INTO receipts(date, merchant, merchant_tax_id, total, net, vat, currency, payment_method, category, items, "
            "vat_lines, file_path, original_name, status, confidence, notes, raw, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (d.get("date"), d.get("merchant") or "", d.get("merchant_tax_id") or "", d.get("total"), d.get("net"), d.get("vat"),
             d["currency"], d.get("payment_method") or "", d.get("category") or "Uncategorized", json.dumps(d.get("items") or []),
             json.dumps(d.get("vat_lines") or []), rel(path), original_name, "review", d.get("confidence") or "",
             notes, json.dumps(d), now())).lastrowid
        doc_fx_update(c, "receipts", rid)
        l4 = cards.norm(d.get("card_last4"))
        if l4:
            c.execute("UPDATE receipts SET card_last4 = ? WHERE id = ?", (l4, rid))
            if (d.get("payment_method") or "") == "cash":   # a receipt that shows card digits was not paid in cash
                c.execute("UPDATE receipts SET payment_method = 'card' WHERE id = ?", (rid,))
                d["payment_method"] = "card"
        if (d.get("payment_method") or "") == "cash" and _card_payment_exists(c, rid):
            # the bank has a matching card payment: the "cash" reading was wrong (e.g. "kontantnota" = paid on the spot)
            c.execute("UPDATE receipts SET payment_method = 'card', notes = notes || ? WHERE id = ?",
                      (" Bank shows a matching card payment, so it is not booked as cash.", rid))
        booked = cash.sync_receipt(c, rid)  # paid in cash -> straight into the cash book
    return rid, booked


def save_invoice(path: Path, original_name: str, d: dict, direction: str = "", notes_prefix: str = "") -> int:
    dirn = direction or (d.get("direction") if d.get("direction") in ("sales", "purchase") else "purchase")
    party = d.get("merchant") if dirn == "purchase" else (d.get("buyer_name") or "")
    party_tax = d.get("merchant_tax_id") if dirn == "purchase" else d.get("buyer_tax_id")
    notes = (notes_prefix + " " + (d.get("notes") or "")).strip()
    with db() as c:
        iid = c.execute(
            "INSERT INTO invoices(direction, number, counterparty, counterparty_tax_id, issue_date, due_date, net, vat, total, "
            "currency, category, status, file_path, original_name, vat_lines, notes, raw, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (dirn, d.get("invoice_number") or "", party or "", party_tax or "", d.get("date"), d.get("due_date"), d.get("net"),
             d.get("vat"), d.get("total"), d["currency"], d.get("category") or "Uncategorized", "unpaid", rel(path),
             original_name, json.dumps(d.get("vat_lines") or []), notes, json.dumps(d), now())).lastrowid
        doc_fx_update(c, "invoices", iid)
    return iid


def _card_payment_exists(c, rid: int) -> bool:
    from .reconcile import find_candidates, linked_tx_ids
    r = c.execute("SELECT * FROM receipts WHERE id = ?", (rid,)).fetchone()
    cands = find_candidates(c, "receipt", r, linked_tx_ids(c))
    return any(x["score"] >= 0.75 and x["transaction"]["account_id"] != cash.account_id(c) for x in cands)
