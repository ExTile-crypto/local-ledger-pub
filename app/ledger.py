"""Base-currency (e.g. DKK) amounts for transactions, receipts and invoices."""
from .db import db, get_settings
from .fx import safe_rate


def base_currency() -> str:
    return (get_settings()["base_currency"] or "DKK").upper()


def tx_fx(row: dict, base: str | None = None) -> dict:
    """Fill fx_rate / fx_source / amount_base on a transaction dict (keeps a manual rate)."""
    base = base or base_currency()
    if row.get("fx_source") == "manual" and row.get("fx_rate"):
        rate, src = row["fx_rate"], "manual"
    else:
        rate, src = safe_rate(row.get("currency") or base, base, row.get("date"))
    row["fx_rate"], row["fx_source"] = rate, src
    row["amount_base"] = round(row["amount"] * rate, 2) if rate is not None and row.get("amount") is not None else None
    return row


def doc_fx(row: dict, date_key: str, base: str | None = None) -> dict:
    """Same for receipts (date) and invoices (issue_date): total/net/vat -> *_base."""
    base = base or base_currency()
    if row.get("fx_source") == "manual" and row.get("fx_rate"):
        rate, src = row["fx_rate"], "manual"
    else:
        rate, src = safe_rate(row.get("currency") or base, base, row.get(date_key))
    row["fx_rate"], row["fx_source"] = rate, src
    for k in ("total", "net", "vat"):
        v = row.get(k)
        row[k + "_base"] = round(v * rate, 2) if rate is not None and isinstance(v, (int, float)) else None
    return row


def recompute(force: bool = False) -> dict:
    """Fill missing base amounts; force=True recalculates everything (e.g. after changing the base currency)."""
    base = base_currency()
    n = {"transactions": 0, "receipts": 0, "invoices": 0, "missing": 0}
    with db() as c:
        where = "" if force else " WHERE amount_base IS NULL OR fx_source IN ('', 'missing')"
        for t in c.execute("SELECT id, date, amount, currency, fx_rate, fx_source FROM transactions" + where).fetchall():
            if force:
                t["fx_source"] = ""
            tx_fx(t, base)
            c.execute("UPDATE transactions SET fx_rate=?, fx_source=?, amount_base=? WHERE id=?",
                      (t["fx_rate"], t["fx_source"], t["amount_base"], t["id"]))
            n["transactions"] += 1
            n["missing"] += t["fx_source"] == "missing"
        for table, dk in (("receipts", "date"), ("invoices", "issue_date")):
            where = "" if force else " WHERE (total IS NOT NULL AND total_base IS NULL) OR fx_source IN ('', 'missing')"
            for r in c.execute(f"SELECT id, {dk}, total, net, vat, currency, fx_rate, fx_source FROM {table}" + where).fetchall():
                if force:
                    r["fx_source"] = ""
                doc_fx(r, dk, base)
                c.execute(f"UPDATE {table} SET fx_rate=?, fx_source=?, total_base=?, net_base=?, vat_base=? WHERE id=?",
                          (r["fx_rate"], r["fx_source"], r["total_base"], r["net_base"], r["vat_base"], r["id"]))
                n[table] += 1
    return n
