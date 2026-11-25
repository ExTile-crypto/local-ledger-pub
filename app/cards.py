"""Payment cards per account (last 4 digits only), used to tell which account paid a receipt.

Receipts print the last 4 digits of the card ("VISA ****4134"). Bank exports usually don't show the card at all,
so the digits are used on the receipt side: a receipt paid with a linked card is only matched against that
account's transactions, and an unmatched one can say why ("no statement imported for Lunar main").
Apple Pay / Google Pay show their own device number, so an account can have several cards.
"""
import json
import re

from .db import now


def norm(value) -> str:
    """'**** **** **** 4134', 'XXXX4134', '4134' -> '4134'; anything without 4 digits -> ''."""
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-4:] if len(digits) >= 4 else ""


def account_for(c, last4: str) -> dict | None:
    if not last4:
        return None
    return c.execute("SELECT a.* FROM account_cards k JOIN accounts a ON a.id = k.account_id WHERE k.last4 = ? "
                     "ORDER BY k.id LIMIT 1", (last4,)).fetchone()


def is_ignored(c, last4: str) -> bool:
    return bool(last4) and bool(c.execute("SELECT 1 FROM ignored_cards WHERE last4 = ?", (last4,)).fetchone())


def ignore(c, last4: str, note: str = ""):
    l4 = norm(last4)
    if not l4:
        raise ValueError("Enter the last 4 digits of the card")
    c.execute("DELETE FROM account_cards WHERE last4 = ?", (l4,))
    c.execute("INSERT OR REPLACE INTO ignored_cards(last4, note, created_at) VALUES (?,?,?)", (l4, note[:100], now()))


def ignored(c) -> list[dict]:
    return c.execute("SELECT i.*, (SELECT COUNT(*) FROM receipts r WHERE r.card_last4 = i.last4) AS receipts "
                     "FROM ignored_cards i ORDER BY last4").fetchall()


def all_cards(c) -> list[dict]:
    return c.execute("SELECT k.*, a.name AS account FROM account_cards k JOIN accounts a ON a.id = k.account_id "
                     "ORDER BY a.name, k.last4").fetchall()


def add(c, account_id: int, last4: str, label: str = "") -> int:
    l4 = norm(last4)
    if not l4:
        raise ValueError("Enter the last 4 digits of the card")
    c.execute("DELETE FROM account_cards WHERE last4 = ?", (l4,))   # a card belongs to one account
    c.execute("DELETE FROM ignored_cards WHERE last4 = ?", (l4,))
    return c.execute("INSERT INTO account_cards(account_id, last4, label, created_at) VALUES (?,?,?,?)",
                     (account_id, l4, label.strip()[:60], now())).lastrowid


def unknown(c) -> list[dict]:
    """Card digits seen on receipts that are not linked to an account yet, with a suggested account:
    the one where receipts paid with that card were already matched."""
    rows = c.execute(
        "SELECT card_last4 AS last4, COUNT(*) AS receipts, MIN(date) AS first, MAX(date) AS last FROM receipts "
        "WHERE card_last4 != '' AND card_last4 NOT IN (SELECT last4 FROM account_cards) "
        "AND card_last4 NOT IN (SELECT last4 FROM ignored_cards) GROUP BY card_last4 "
        "ORDER BY receipts DESC").fetchall()
    for r in rows:
        hit = c.execute(
            "SELECT t.account_id, a.name, COUNT(*) AS n FROM receipts r JOIN transactions t ON t.id = r.transaction_id "
            "JOIN accounts a ON a.id = t.account_id WHERE r.card_last4 = ? AND a.kind != 'cash' "
            "GROUP BY t.account_id ORDER BY n DESC LIMIT 1", (r["last4"],)).fetchone()
        r["suggested_account_id"] = hit["account_id"] if hit else None
        r["suggested_reason"] = f"{hit['n']} receipt(s) with this card matched payments in {hit['name']}" if hit else ""
    return rows


def coverage(c, account_id: int) -> dict:
    return c.execute("SELECT COUNT(*) AS n, MIN(date) AS first, MAX(date) AS last FROM transactions WHERE account_id = ?",
                     (account_id,)).fetchone()


def why_unmatched(c, r: dict) -> str:
    """Plain-language reason a receipt has no bank transaction."""
    if (r.get("payment_method") or "") == "cash":
        return "Paid in cash"
    l4 = r.get("card_last4") or ""
    if not l4:
        return "No card shown on the receipt - paid from an account whose statement isn't imported, or the amount differs"
    if is_ignored(c, l4):
        return f"Card ••{l4} isn't on any of your accounts (ignored), so it won't appear in your statements"
    acc = account_for(c, l4)
    if not acc:
        return f"Card ••{l4} isn't linked to an account yet"
    cov = coverage(c, acc["id"])
    if not cov["n"]:
        return f"Card ••{l4} = {acc['name']}: no statement imported for that account"
    d = (r.get("date") or "")[:10]
    if d and (d < cov["first"] or d > cov["last"]):
        return f"Card ••{l4} = {acc['name']}: the imported statement covers {cov['first']} – {cov['last']}, not {d}"
    return f"Card ••{l4} = {acc['name']}: no payment of this amount found there around {d}"


def backfill(c):
    """Fill receipts.card_last4 from what the model already read (raw JSON)."""
    for r in c.execute("SELECT id, raw FROM receipts WHERE (card_last4 IS NULL OR card_last4 = '') AND raw LIKE '%card_last4%'").fetchall():
        try:
            l4 = norm((json.loads(r["raw"]) or {}).get("card_last4"))
        except (TypeError, ValueError):
            l4 = ""
        if l4:
            c.execute("UPDATE receipts SET card_last4 = ? WHERE id = ?", (l4, r["id"]))
