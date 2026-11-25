#!/usr/bin/env python3
"""Check the prompts against the REAL model with the bundled sample documents.

Run it on the server while the model is loaded in Unsloth Studio (takes a few minutes):

    cd /opt/local-ledger && sudo -u ledger .venv/bin/python tests/check_prompts.py

Nothing is written to the ledger: the samples are read and compared with what is printed on them.
Every line is PASS or FAIL; a FAIL shows what the model answered. Use it after editing a skill, or after
changing the model, to see whether reading got better or worse.
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.categorize import categorize_rows          # noqa: E402
from app.db import category_kinds                    # noqa: E402
from app.documents import extract_document           # noqa: E402
from app.importers import parse_statement            # noqa: E402
from app.llm import LLM, LLMError                    # noqa: E402

S = Path(__file__).resolve().parent.parent / "samples"
DOCS = [
    ("kvittering-netto.jpg", "receipt", {"total": 187.45, "vat": 37.49, "date": "2026-10-03", "currency": "DKK",
                                         "payment_method": "card", "card_last4": "4321", "merchant~": "netto"}),
    ("kvittering-cafe-kontant.jpg", "receipt", {"total": 165.0, "vat": 33.0, "date": "2026-11-02", "currency": "DKK",
                                                "payment_method": "cash"}),
    ("receipt-shell-flensburg-EUR.jpg", "receipt", {"total": 61.4, "vat": 9.8, "date": "2026-10-10", "currency": "EUR",
                                                    "payment_method": "card", "card_last4": "4321", "merchant~": "shell"}),
    ("invoice-design-studio-berlin-EUR.pdf", "invoice", {"total": 800.0, "currency": "EUR", "date": "2026-10-12",
                                                         "due_date": "2026-10-26", "invoice_number": "DSB-2026-118"}),
    ("faktura-2026-045-salg.pdf", "invoice", {"total": 25625.0, "net": 20500.0, "vat": 5125.0, "date": "2026-11-04",
                                              "due_date": "2026-11-18", "invoice_number": "2026-045"}),
]
# payees that no keyword decides, with the KIND (or exact category) a careful bookkeeper would choose
PAYEES = [
    ("MobilePay Jens Hansen", 150.00, {"Friends & family", "Uncategorized"}),
    ("Jens Hansen", -200.00, {"Friends & family", "Uncategorized"}),
    ("Afterpay", -119.00, {"Shopping & clothing", "Other expenses"}),
    ("Aire Ancient Baths", 350.00, "expense"),            # a refund: same expense category, never income
    ("SILVERBACK Sportsgym", -299.00, {"Health & personal", "Leisure, games & hobbies"}),
    ("Lunar Plus", -69.00, {"Bank fees"}),
    ("Sold Solana", 1200.00, "transfer"),
    ("GLS", -45.00, {"Shopping & clothing", "Other expenses"}),
    ("Hundige Apotek", -251.00, {"Health & personal"}),
    ("Wolt", -189.00, {"Meals & entertainment"}),
]
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   -> {detail}" if detail and not ok else ""))


def same(got, want):
    if isinstance(want, float):
        return isinstance(got, (int, float)) and abs(got - want) < 0.011
    return str(got or "").strip() == want


async def main():
    try:
        using = await LLM().resolve_model()
    except LLMError as e:
        sys.exit(f"The model is not available: {e}")
    print(f"Model: {using}\n")
    for fname, kind, want in DOCS:
        t = time.time()
        try:
            d = await extract_document(S / fname, kind)
        except Exception as e:
            check(fname, False, f"{type(e).__name__}: {e}")
            continue
        print(f"{fname}  ({time.time() - t:.0f} s, warnings: {len(d.get('warnings') or [])})")
        for key, value in want.items():
            if key.endswith("~"):
                got = d.get(key[:-1])
                check(f"{key[:-1]} contains '{value}'", value in str(got or "").lower(), repr(got))
            else:
                check(f"{key} = {value}", same(d.get(key), value), repr(d.get(key)))
    t = time.time()
    try:
        r = await parse_statement(S / "danske-bank-kontoudtog-november.pdf", None)
        txs = r["transactions"]
        print(f"danske-bank-kontoudtog-november.pdf  ({time.time() - t:.0f} s)")
        check("6 transactions", len(txs) == 6, str(len(txs)))
        check("amounts add up to +788.38", abs(sum(x["amount"] for x in txs) - 788.38) < 0.011, f"{sum(x['amount'] for x in txs):.2f}")
        check("balances reconcile", (r["meta"].get("statement") or {}).get("balance_check") == "ok", str(r["meta"].get("statement")))
        hotel = next((x for x in txs if "ADLON" in (x["description"] + x["counterparty"]).upper()), {})
        check("foreign amount EUR 120.00 kept", abs(abs(hotel.get("orig_amount") or 0) - 120.0) < 0.011 and hotel.get("orig_currency") == "EUR",
              f"{hotel.get('orig_amount')} {hotel.get('orig_currency')}")
    except Exception as e:
        check("bank statement PDF", False, f"{type(e).__name__}: {e}")
    rows = [{"date": "2026-11-01", "amount": a, "currency": "DKK", "counterparty": n, "description": n} for n, a, _ in PAYEES]
    t = time.time()
    warnings = await categorize_rows(rows)
    kinds = category_kinds()
    print(f"categorization of {len(rows)} payees  ({time.time() - t:.0f} s)" + (f"  {warnings}" if warnings else ""))
    for row, (name, amount, want) in zip(rows, PAYEES):
        got = row["category"]
        ok = kinds.get(got) == want if isinstance(want, str) else got in want
        check(f"{name} ({amount:+.0f}) -> {want if isinstance(want, str) else ' / '.join(sorted(want))}", ok, got)
    print(f"\n{sum(results)} of {len(results)} checks passed.")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
