"""Match receipts and invoices to bank transactions (deterministic, no model needed)."""
import re
from datetime import date, timedelta
from difflib import SequenceMatcher

from .db import db, get_settings

STOP = {"sp", "zoo", "z", "o", "gmbh", "bv", "ltd", "inc", "llc", "sa", "ag", "the", "and", "s", "a", "co",
        "sklep", "store", "shop", "market", "payment", "card", "karta", "platnosc", "zakup"}


def _tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9ąćęłńóśźżäöüß]{3,}", (s or "").lower()) if t not in STOP}


def name_similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    if ta & tb:
        return 1.0
    best = max(SequenceMatcher(None, x, y).ratio() for x in ta for y in tb)
    return best if best >= 0.75 else 0.0


def _d(s):
    try:
        return date.fromisoformat((s or "")[:10])
    except ValueError:
        return None


def linked_tx_ids(c) -> set[int]:
    ids = {r["transaction_id"] for r in c.execute(
        "SELECT transaction_id FROM receipts WHERE transaction_id IS NOT NULL "
        "UNION SELECT transaction_id FROM invoices WHERE transaction_id IS NOT NULL")}
    return ids


def find_candidates(c, doc_type: str, doc: dict, exclude: set[int]) -> list[dict]:
    s = get_settings()
    total = doc.get("total")
    if total is None:
        return []
    if doc_type == "receipt":
        days = int(s["receipt_match_days"] or 5)
        d = _d(doc.get("date"))
        if not d:
            return []
        lo, hi = d - timedelta(days=2), d + timedelta(days=days)
        want = -abs(total)
        name = doc.get("merchant") or ""
    else:
        d = _d(doc.get("issue_date"))
        if not d:
            return []
        due = _d(doc.get("due_date")) or d + timedelta(days=30)
        lo, hi = d - timedelta(days=5), due + timedelta(days=90)
        want = abs(total) if doc["direction"] == "sales" else -abs(total)
        name = doc.get("counterparty") or ""
    rows = c.execute("SELECT * FROM transactions WHERE date BETWEEN ? AND ? AND amount * ? > 0",
                     (lo.isoformat(), hi.isoformat(), want)).fetchall()
    if doc_type == "receipt" and doc.get("card_last4"):
        from .cards import account_for, is_ignored
        if is_ignored(c, doc["card_last4"]):
            return []  # a card that isn't on any of the user's accounts never shows up in their statements
        acc = account_for(c, doc["card_last4"])
        if acc:  # paid with a known card: the payment can only be in that card's account
            rows = [t for t in rows if t["account_id"] == acc["id"]]
    dcur = (doc.get("currency") or "").upper()
    want_base = doc.get("total_base")
    out = []
    for t in rows:
        if t["id"] in exclude:
            continue
        tcur = (t["currency"] or "").upper()
        if not dcur or dcur == tcur:  # same currency: amounts must be identical
            if abs(t["amount"] - want) >= 0.011:
                continue
            base_score, how = 0.55, "same amount"
        elif (t.get("orig_currency") or "").upper() == dcur and t.get("orig_amount") is not None \
                and abs(abs(t["orig_amount"]) - abs(want)) < 0.011:
            base_score, how = 0.55, f"same {dcur} amount (card payment abroad)"
        elif want_base and t.get("amount_base") is not None and abs(abs(t["amount_base"]) - abs(want_base)) <= 0.03 * abs(want_base):
            # converted amounts within 3% (bank exchange margin and card fees)
            base_score, how = 0.45, f"amount within {abs(abs(t['amount_base']) / abs(want_base) - 1) * 100:.1f}% after currency conversion"
        else:
            continue
        text = f"{t['counterparty']} {t['description']}"
        sim = name_similarity(name, text)
        gap = abs((_d(t["date"]) - d).days)
        score = base_score + 0.35 * sim + (0.10 * max(0.0, 1 - gap / 7) if doc_type == "receipt" else 0.05)
        num = (doc.get("number") or "").lower()
        if doc_type == "invoice" and len(num) >= 3 and num in text.lower():
            score += 0.3
        reason = f"{how}, {gap} day(s) apart" if doc_type == "receipt" else f"{how} within payment window"
        if sim:
            reason += ", name matches"
        out.append({"transaction": t, "score": round(min(score, 1.0), 2), "reason": reason})
    return sorted(out, key=lambda x: -x["score"])


def suggestions(c) -> list[dict]:
    used = linked_tx_ids(c)
    out = []
    for r in c.execute("SELECT * FROM receipts WHERE transaction_id IS NULL AND total IS NOT NULL").fetchall():
        for cand in find_candidates(c, "receipt", r, used)[:3]:
            out.append({"doc_type": "receipt", "doc": r, **cand})
    for inv in c.execute("SELECT * FROM invoices WHERE transaction_id IS NULL AND total IS NOT NULL").fetchall():
        for cand in find_candidates(c, "invoice", inv, used)[:3]:
            out.append({"doc_type": "invoice", "doc": inv, **cand})
    return sorted(out, key=lambda x: -x["score"])


def link(c, doc_type: str, doc_id: int, tx_id: int | None):
    table = "receipts" if doc_type == "receipt" else "invoices"
    c.execute(f"UPDATE {table} SET transaction_id = ? WHERE id = ?", (tx_id, doc_id))
    if doc_type == "invoice":
        c.execute("UPDATE invoices SET status = ? WHERE id = ?", ("paid" if tx_id else "unpaid", doc_id))
    if tx_id:
        # the document is a better source for the category than a guess from the bank text
        rc = c.execute(f"SELECT category FROM {table} WHERE id = ?", (doc_id,)).fetchone()
        if rc and rc["category"] and rc["category"] != "Uncategorized":
            c.execute("UPDATE transactions SET category = ?, category_source = ? "
                      "WHERE id = ? AND (category = 'Uncategorized' OR category_source IN ('', 'llm', 'keyword'))",
                      (rc["category"], doc_type, tx_id))


def auto_match() -> int:
    """Link documents whose best candidate is clearly right. Returns number of new links."""
    n = 0
    with db() as c:
        sug = suggestions(c)
        taken_tx, taken_doc = set(), set()
        by_doc = {}
        for s in sug:
            by_doc.setdefault((s["doc_type"], s["doc"]["id"]), []).append(s)
        for key, cands in sorted(by_doc.items(), key=lambda kv: -kv[1][0]["score"]):
            best = cands[0]
            runner = cands[1]["score"] if len(cands) > 1 else 0
            tx_id = best["transaction"]["id"]
            if best["score"] >= 0.75 and best["score"] - runner >= 0.15 and tx_id not in taken_tx:
                link(c, key[0], key[1], tx_id)
                taken_tx.add(tx_id)
                taken_doc.add(key)
                n += 1
    return n


def missing_documents(c, start=None, end=None, min_amount=0.0) -> list[dict]:
    """Money-out bank lines with no receipt/invoice attached (excluding transfers, taxes, fees)."""
    q = ("SELECT t.* FROM transactions t LEFT JOIN categories k ON k.name = t.category "
         "WHERE t.amount < 0 AND abs(t.amount) >= ? AND COALESCE(k.kind, 'other') NOT IN ('transfer') "
         "AND t.category NOT IN ('Bank fees', 'Taxes & social security') "
         "AND t.id NOT IN (SELECT transaction_id FROM receipts WHERE transaction_id IS NOT NULL) "
         "AND t.id NOT IN (SELECT transaction_id FROM invoices WHERE transaction_id IS NOT NULL)")
    args = [min_amount]
    if start:
        q += " AND t.date >= ?"
        args.append(start)
    if end:
        q += " AND t.date <= ?"
        args.append(end)
    return c.execute(q + " ORDER BY t.date DESC", args).fetchall()
