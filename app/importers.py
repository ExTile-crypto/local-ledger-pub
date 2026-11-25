"""Bank statement parsing.

CSV / XLSX: the model only works out the column layout; Python then parses every row exactly.
PDF / text / images: the model extracts the transactions page by page; Python checks the result
against running balances and opening/closing balances.
"""
import csv
import hashlib
import io
import re
import subprocess
import tempfile
from datetime import date, datetime
from pathlib import Path

from .categorize import categorize_rows
from .db import db, get_settings
from .ledger import tx_fx
from .llm import LLM
from .ocr import cross_check, image_content
from .skills import system_prompt

TABULAR = {".csv", ".tsv", ".txt"}
SHEETS = {".xlsx", ".xlsm"}
IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
NUM = ["number", "null"]
STR = ["string", "null"]


# ---------- low level helpers ----------

def decode(raw: bytes) -> str:
    from .packs import pack
    for enc in pack()["encodings"]:  # UTF-8 first, then the Windows code page banks of that country still use
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", "replace")


def read_delimited(text: str) -> list[list[str]]:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    sample = "\n".join(lines[:40])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
        delim = dialect.delimiter
    except csv.Error:
        delim = max(";,\t|", key=lambda d: sample.count(d))
    return [[c.strip() for c in row] for row in csv.reader(io.StringIO("\n".join(lines)), delimiter=delim)]


def read_xlsx(path: Path) -> list[list[str]]:
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = []
    for r in ws.iter_rows(values_only=True):
        cells = []
        for v in r:
            if isinstance(v, datetime):
                v = v.date().isoformat()
            elif isinstance(v, date):
                v = v.isoformat()
            cells.append("" if v is None else str(v))
        if any(cells):
            rows.append(cells)
    return rows


def looks_tabular(text: str) -> bool:
    lines = [ln for ln in text.splitlines()[:30] if ln.strip()]
    if len(lines) < 3:
        return False
    for d in (";", ",", "\t", "|"):
        counts = [ln.count(d) for ln in lines]
        if sorted(counts)[len(counts) // 2] >= 2:
            return True
    return False


def parse_number(s, dec: str = "."):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip().replace(" ", "").replace(" ", "").replace("'", "")
    if not t:
        return None
    neg = t.startswith("(") and t.endswith(")") or t.endswith("-") or t.startswith("-") or t.startswith("−")
    t = re.sub(r"[^0-9.,]", "", t)
    if not t or not re.search(r"\d", t):
        return None
    if dec == ",":
        if "," not in t and re.fullmatch(r"\d+\.\d{1,2}", t):
            pass  # plain 1234.56 even though the file mostly uses commas
        else:
            t = t.replace(".", "").replace(",", ".")
    else:
        t = t.replace(",", "")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


DATE_FORMATS = ["%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y", "%Y.%m.%d", "%Y/%m/%d", "%m/%d/%Y",
                "%d.%m.%y", "%d-%m-%y", "%d/%m/%y", "%Y%m%d", "%d %b %Y", "%d %B %Y", "%b %d, %Y"]


def parse_date(s: str, fmt: str | None = None) -> str | None:
    s = (s or "").strip()
    if not s:
        return None
    s = s.split("T")[0].split(" ")[0] if re.match(r"\d{4}-\d{2}-\d{2}[T ]", s) else s
    for f in ([fmt] if fmt else []) + DATE_FORMATS:
        try:
            return datetime.strptime(s, f).date().isoformat()
        except (ValueError, TypeError):
            continue
    return None


FIAT = ("EUR|USD|GBP|SEK|NOK|DKK|ISK|CHF|PLN|CZK|HUF|RON|BGN|TRY|JPY|CNY|HKD|SGD|AUD|NZD|CAD|ZAR|THB|INR|IDR|MYR|PHP|KRW|"
        "BRL|MXN|ILS")
ORIG_RE = re.compile(r"\b(" + FIAT + r")\s?(\d{1,3}(?:[ .,']\d{3})*(?:[.,]\d{1,2})?)\b")


def detect_original(row: dict):
    """Fallback: find 'USD 21,00' style foreign amounts in the description of card payments abroad."""
    if row.get("orig_amount") is not None or row.get("orig_currency"):
        return
    m = ORIG_RE.search(f"{row.get('description', '')} {row.get('counterparty', '')}")
    if not m or m.group(1) == (row.get("currency") or "").upper():
        return
    txt = m.group(2)
    dec = "," if re.search(r",\d{1,2}$", txt) else "."
    v = parse_number(txt, dec)
    if v:
        row["orig_amount"] = -abs(v) if row["amount"] < 0 else abs(v)
        row["orig_currency"] = m.group(1)


# what banks put in front of the payee - Danish, Polish, German and English forms (a heuristic, shared by all packs)
CP_PREFIX = re.compile(
    r"^(dankort-nota|dankort|visa/dankort|visa køb|visa|mastercard køb|mastercard|maestro|kortkøb|købt|card payment|"
    r"betalingsservice|bs-betaling|mobilepay|indbetaling fra|indbetaling|udl\. overførsel|udlandsoverførsel|"
    r"overførsel fra|overførsel til|overførsel|sepa dd|sepa ct|"
    r"zakup przy użyciu karty|zakup przy uzyciu karty|płatność kartą|platnosc karta|transakcja kartą|transakcja karta|"
    r"zakup blik|płatność blik|platnosc blik|przelew na telefon|przelew przychodzący|przelew wychodzący|polecenie zapłaty|"
    r"zlecenie stałe|kartenzahlung|lastschrift|überweisung|pos purchase|card purchase|direct debit)\s+", re.I)


def guess_counterparty(text: str) -> str:
    """'Dankort-nota NETTO NØRREBRO 4521' -> 'Netto Nørrebro'; '' when the text has no known prefix."""
    t = re.sub(r"\s+", " ", text or "").strip()
    m = CP_PREFIX.match(t)
    if not m:
        return ""
    t = t[m.end():]
    t = ORIG_RE.sub("", t)                                         # foreign amount
    t = re.sub(r"\b(kurs|rate)\s*[\d.,]+", "", t, flags=re.I)        # exchange rate
    t = re.sub(r"\b(faktura|fakt\.?|invoice|nota|ref\.?|kunde ?nr\.?)\b.*$", "", t, flags=re.I)
    t = re.sub(r"(\s+\d[\d/.-]*)+$", "", t).strip(" -,.")          # trailing store/terminal numbers, dates
    if len(t) < 2 or t.lower().startswith(("til ", "fra ")):
        return ""
    def fix(w):
        if any(ch.islower() for ch in w) or len(w) <= 3:   # already mixed case, or an acronym (TDC, DSB)
            return w
        return w.lower() if "." in w else w.capitalize()  # GITHUB.COM -> github.com, NETTO -> Netto
    return " ".join(fix(w) for w in t.split())[:80]


def fingerprint(account_id, row: dict, n: int) -> str:
    key = "|".join([str(account_id or ""), row["date"], f"{round(row['amount'], 2):.2f}",
                    re.sub(r"\s+", " ", (row.get("description") or "").lower()).strip()[:120], str(n)])
    return hashlib.sha1(key.encode()).hexdigest()


def add_fingerprints(rows: list[dict], account_id):
    seen = {}
    for r in rows:
        base = (r["date"], round(r["amount"], 2), (r.get("description") or "").lower())
        seen[base] = seen.get(base, 0) + 1
        r["fingerprint"] = fingerprint(account_id, r, seen[base])


# ---------- tabular (CSV / XLSX) ----------

MAPPING_SCHEMA = {
    "type": "object",
    "properties": {
        "header_row": {"type": "integer"},
        "first_data_row": {"type": "integer"},
        "date_col": {"type": "integer"},
        "date_format": {"type": "string"},
        "amount_mode": {"type": "string", "enum": ["single", "debit_credit", "amount_and_type"]},
        "amount_col": {"type": "integer"},
        "debit_col": {"type": "integer"},
        "credit_col": {"type": "integer"},
        "type_col": {"type": "integer"},
        "debit_markers": {"type": "array", "items": {"type": "string"}},
        "decimal_separator": {"type": "string", "enum": [".", ","]},
        "description_cols": {"type": "array", "items": {"type": "integer"}},
        "counterparty_col": {"type": "integer"},
        "reference_col": {"type": "integer"},
        "balance_col": {"type": "integer"},
        "currency_col": {"type": "integer"},
        "orig_amount_col": {"type": "integer"},
        "orig_currency_col": {"type": "integer"},
        "currency": {"type": "string"},
        "account_iban": {"type": "string"},
        "account_name": {"type": "string"},
        "notes": {"type": "string"},
    },
    "required": ["header_row", "first_data_row", "date_col", "date_format", "amount_mode", "amount_col",
                 "debit_col", "credit_col", "type_col", "debit_markers", "decimal_separator", "description_cols",
                 "counterparty_col", "reference_col", "balance_col", "currency_col", "orig_amount_col", "orig_currency_col",
                 "currency", "account_iban",
                 "account_name", "notes"],
}

MAPPING_TASK = """You are given the first and last rows of a bank export as numbered rows; cells are separated by ' | '
and prefixed with their 0-based column index like [3]. Work out the column layout and return the mapping JSON.
- header_row: row number of the column header line (-1 if none). first_data_row: first real transaction row.
- date_col: booking/transaction date column (prefer booking date over value date). date_format: Python strptime format, e.g. %d.%m.%Y or %Y-%m-%d.
- amount_mode 'single': one signed amount column (amount_col). 'debit_credit': separate money-out (debit_col) and money-in (credit_col) columns.
  'amount_and_type': unsigned amount plus a column (type_col) whose values in debit_markers mean money out.
- orig_amount_col / orig_currency_col: columns with the original foreign-currency amount of card payments abroad, if the export has them (-1 otherwise).
- Use -1 for any column that does not exist. description_cols: all columns that describe the transaction (title, details), NOT the counterparty column.
- currency: ISO code if the file shows one (constant or default), else empty. account_iban/account_name: if visible in the preamble, else empty.
- notes: anything odd (e.g. footer/summary rows to ignore)."""


def _sample(rows: list[list[str]]) -> str:
    idx = list(range(min(len(rows), 30)))
    if len(rows) > 35:
        idx += list(range(len(rows) - 5, len(rows)))
    out = []
    for i in idx:
        cells = " | ".join(f"[{j}] {c[:110]}" for j, c in enumerate(rows[i]))
        out.append(f"row {i}: {cells}")
        if i == 29 and len(rows) > 35:
            out.append("...")
    return "\n".join(out)


def _cell(row, i):
    return row[i] if 0 <= i < len(row) else ""


def apply_mapping(rows: list[list[str]], m: dict, default_currency: str) -> tuple[list[dict], int]:
    out, skipped = [], 0
    dec = m.get("decimal_separator", ".")
    markers = [x.strip().lower() for x in m.get("debit_markers") or [] if x.strip()]
    start = max(m.get("first_data_row", 0), m.get("header_row", -1) + 1)
    for row in rows[start:]:
        d = parse_date(_cell(row, m["date_col"]), m.get("date_format"))
        if not d:
            skipped += 1
            continue
        mode = m["amount_mode"]
        if mode == "debit_credit":
            deb = parse_number(_cell(row, m["debit_col"]), dec)
            cre = parse_number(_cell(row, m["credit_col"]), dec)
            if deb is None and cre is None:
                skipped += 1
                continue
            amount = (abs(cre) if cre else 0.0) - (abs(deb) if deb else 0.0)
        else:
            amount = parse_number(_cell(row, m["amount_col"]), dec)
            if amount is None:
                skipped += 1
                continue
            if mode == "amount_and_type":
                typ = _cell(row, m["type_col"]).strip().lower()
                amount = -abs(amount) if any(typ == k or typ.startswith(k) for k in markers) else abs(amount)
        cp = _cell(row, m["counterparty_col"]).strip()
        desc = " ".join(_cell(row, i).strip() for i in m.get("description_cols") or [] if _cell(row, i).strip())
        cur = (_cell(row, m["currency_col"]).strip().upper() or m.get("currency") or default_currency)[:3]
        oa = parse_number(_cell(row, m.get("orig_amount_col", -1)), dec)
        oc = _cell(row, m.get("orig_currency_col", -1)).strip().upper()[:3]
        out.append({
            "date": d, "amount": round(amount, 2), "currency": cur,
            "description": re.sub(r"\s+", " ", desc)[:500], "counterparty": cp[:200],
            "reference": _cell(row, m["reference_col"]).strip()[:120],
            "balance_after": parse_number(_cell(row, m["balance_col"]), dec),
            "orig_amount": -abs(oa) if (oa is not None and amount < 0) else oa, "orig_currency": oc if oa is not None else "",
        })
    return out, skipped


async def parse_tabular(rows: list[list[str]]) -> dict:
    s = get_settings()
    llm = LLM()
    sys = system_prompt("bank-statement-import", task=MAPPING_TASK, context=False)
    m = await llm.extract(sys, _sample(rows), MAPPING_SCHEMA, thinking=True, max_tokens=8000)
    txs, skipped = apply_mapping(rows, m, s["base_currency"])
    warnings = []
    if not txs:
        raise ValueError("Could not read any transactions with the detected layout. "
                         f"Model notes: {m.get('notes', '')}")
    if skipped > max(3, len(txs) * 0.2):
        warnings.append(f"{skipped} rows were skipped (no valid date/amount) - check that nothing is missing.")
    if all(t["amount"] > 0 for t in txs) and len(txs) > 3:
        warnings.append("All amounts are positive - money out may not have been detected. Check the signs.")
    meta = {"method": "column mapping", "mapping": m, "account_iban": m.get("account_iban", ""),
            "account_name": m.get("account_name", ""), "currency": m.get("currency", "")}
    return {"transactions": txs, "meta": meta, "warnings": warnings}


# ---------- documents (PDF / text / images) ----------

TX_SCHEMA = {
    "type": "object",
    "properties": {
        "statement": {"type": "object", "properties": {
            "bank_name": {"type": STR}, "account_iban": {"type": STR}, "account_name": {"type": STR},
            "currency": {"type": STR}, "period_start": {"type": STR}, "period_end": {"type": STR},
            "opening_balance": {"type": NUM}, "closing_balance": {"type": NUM}},
            "required": ["bank_name", "account_iban", "account_name", "currency", "period_start", "period_end",
                         "opening_balance", "closing_balance"]},
        "transactions": {"type": "array", "items": {"type": "object", "properties": {
            "date": {"type": "string"}, "description": {"type": "string"}, "counterparty": {"type": "string"},
            "amount": {"type": "number"}, "currency": {"type": STR}, "balance_after": {"type": NUM},
            "reference": {"type": "string"}, "original_amount": {"type": NUM}, "original_currency": {"type": STR}},
            "required": ["date", "description", "counterparty", "amount", "currency", "balance_after", "reference",
                         "original_amount", "original_currency"]}},
    },
    "required": ["statement", "transactions"],
}

EXTRACT_TASK = """Extract every transaction line from the part of a bank statement given below (a statement is sent in
one or more parts; the first line says which part this is).
- amount: negative for money out (debits, payments, card purchases, fees, withdrawals), positive for money in.
- date: booking date as YYYY-MM-DD. If only day/month are printed, take the year from the statement period.
- balance_after: running balance printed on the line, else null.
- currency: the ACCOUNT currency the amount is booked in. For card payments abroad the line often also shows the
  foreign amount (e.g. "USD 21,00 kurs 6,85"): put that in original_amount (same sign as amount) / original_currency, else null.
- Skip opening/closing balance lines, carried-forward balances, page totals and summaries - they are not transactions.
- statement: fill what is visible on this part (opening_balance only if printed here; closing_balance only if printed here); null otherwise.
- A line continued on the next row (long text, reference numbers) is still one transaction.
- Copy text as printed; do not translate. Do not invent lines, and do not leave any out."""


def pdf_pages_text(path: Path) -> list[str]:
    import pdfplumber
    pages = []
    with pdfplumber.open(path) as pdf:
        for p in pdf.pages:
            pages.append(p.extract_text(layout=True) or "")
    return pages


def pdf_pages_images(path: Path, max_pages=20, dpi=150) -> list[bytes]:
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-r", str(dpi), "-png", "-l", str(max_pages), str(path), f"{tmp}/p"],
                       check=True, capture_output=True, timeout=180)
        return [f.read_bytes() for f in sorted(Path(tmp).glob("p*.png"))]


def _chunks(pages: list[str], limit=7000) -> list[str]:
    out, cur = [], ""
    for i, p in enumerate(pages):
        block = f"--- page {i + 1} ---\n{re.sub(r'[ \t]{3,}', '   ', p)}"
        if cur and len(cur) + len(block) > limit:
            out.append(cur)
            cur = ""
        cur += block + "\n"
    if cur.strip():
        out.append(cur)
    return out


def check_balances(txs: list[dict], stmt: dict) -> list[str]:
    """Use running balances to catch (and fix) sign mistakes; compare opening + sum to closing."""
    notes = []
    bal = [t.get("balance_after") for t in txs]
    if sum(b is not None for b in bal) >= 3:
        def matches(order):
            ok = 0
            for a, b in zip(order, order[1:]):
                if bal[a] is not None and bal[b] is not None and abs(bal[a] + txs[b]["amount"] - bal[b]) < 0.011:
                    ok += 1
            return ok
        fwd = list(range(len(txs)))
        order = fwd if matches(fwd) >= matches(fwd[::-1]) else fwd[::-1]
        fixed = 0
        for a, b in zip(order, order[1:]):
            if bal[a] is None or bal[b] is None:
                continue
            amt = txs[b]["amount"]
            if abs(bal[a] + amt - bal[b]) >= 0.011 and abs(bal[a] - amt - bal[b]) < 0.011:
                txs[b]["amount"] = -amt
                fixed += 1
        if fixed:
            notes.append(f"Fixed the sign of {fixed} transaction(s) using the running balance.")
        bad = sum(1 for a, b in zip(order, order[1:]) if bal[a] is not None and bal[b] is not None
                  and abs(bal[a] + txs[b]["amount"] - bal[b]) >= 0.011)
        if bad:
            notes.append(f"{bad} line(s) do not agree with the running balance - review them.")
    ob, cb = stmt.get("opening_balance"), stmt.get("closing_balance")
    if ob is not None and cb is not None:
        diff = round(ob + sum(t["amount"] for t in txs) - cb, 2)
        stmt["balance_check"] = "ok" if abs(diff) < 0.011 else f"off by {diff:+.2f}"
        if abs(diff) >= 0.011:
            notes.append(f"Opening balance + transactions differs from closing balance by {diff:+.2f}. "
                         "A line may be missing or duplicated.")
    return notes


async def parse_document(parts: list, images: bool = False) -> dict:
    """parts: list of (label, content); content is text, or an image (path/bytes) when images=True.
    Reads fast first; if the balance or OCR checks fail, reads the whole document again with full reasoning."""
    llm = LLM()
    prepared, notes, ocr_texts = [], [], []
    for label, content in parts:
        if images:
            prep = await image_content([content])
            content = prep["content"]
            if prep["ocr_text"]:
                ocr_texts.append(prep["ocr_text"])
            notes += [w for w in prep["warnings"] if w not in notes]
        prepared.append((label, content))
    result = await _read_document(llm, prepared, ocr_texts, None)
    if result["issues"] and llm.effort_docs != "xhigh":
        careful = await _read_document(llm, prepared, ocr_texts, "xhigh")
        if len(careful["issues"]) <= len(result["issues"]):
            result = careful
        result["issues"].append("Re-read with full reasoning because the first pass did not pass the checks.")
    result["warnings"] = notes + result.pop("notes") + result.pop("issues")
    return result


async def _read_document(llm, parts: list, ocr_texts: list, effort) -> dict:
    s = get_settings()
    stmt, txs, warnings = {}, [], []
    # the same system prompt for every part, so the model server reuses its cache; the part label goes with the content
    sys = system_prompt("bank-statement-import", task=EXTRACT_TASK, context=False)
    for i, (label, content) in enumerate(parts):
        head = f"Part {i + 1} of {len(parts)} ({label})."
        content = f"{head}\n\n{content}" if isinstance(content, str) else [{"type": "text", "text": head}, *content]
        out = await llm.extract(sys, content, TX_SCHEMA, effort=effort, max_tokens=16000)
        for k, v in (out.get("statement") or {}).items():
            if v is None or v == "":
                continue
            if k == "opening_balance" and "opening_balance" in stmt:
                continue  # keep the first one
            stmt[k] = v  # later parts overwrite closing balance / period end
        for t in out.get("transactions") or []:
            d = parse_date(t.get("date"))
            amt = t.get("amount")
            if not d or not isinstance(amt, (int, float)):
                warnings.append(f"Skipped unreadable line: {t.get('description', '')[:60]}")
                continue
            txs.append({"date": d, "amount": round(float(amt), 2),
                        "currency": (t.get("currency") or stmt.get("currency") or s["base_currency"]).upper()[:3],
                        "description": (t.get("description") or "")[:500], "counterparty": (t.get("counterparty") or "")[:200],
                        "reference": (t.get("reference") or "")[:120], "balance_after": t.get("balance_after"),
                        "orig_amount": t.get("original_amount"), "orig_currency": (t.get("original_currency") or "").upper()[:3]})
    if not txs:
        raise ValueError("No transactions found in this document.")
    balance_notes = check_balances(txs, stmt)
    # a sign corrected via the running balance is reported, but is not a reason to re-read
    fixed = [w for w in balance_notes if w.startswith("Fixed the sign")]
    issues = list(warnings) + [w for w in balance_notes if not w.startswith("Fixed the sign")]
    if ocr_texts:  # amounts the OCR model did not see are worth a second look
        seen = [t for t in txs if not cross_check({"total": t["amount"]}, "\n".join(ocr_texts))]
        missed = len(txs) - len(seen)
        if missed:
            issues.append(f"{missed} amount(s) were not found in the OCR transcription - compare those lines with the scan.")
    meta = {"method": "model extraction" + (" (careful re-read)" if effort == "xhigh" else ""), "statement": stmt,
            "account_iban": stmt.get("account_iban") or "",
            "account_name": stmt.get("account_name") or stmt.get("bank_name") or "", "currency": stmt.get("currency") or ""}
    return {"transactions": txs, "meta": meta, "issues": issues, "notes": fixed}


# ---------- entry point ----------

async def parse_statement(path: Path, account_id: int | None) -> dict:
    ext = path.suffix.lower()
    raw = path.read_bytes()
    if ext in SHEETS:
        result = await parse_tabular(read_xlsx(path))
    elif ext == ".xls":
        raise ValueError("Old .xls files are not supported - save it as .xlsx or .csv first.")
    elif ext == ".pdf":
        pages = pdf_pages_text(path)
        if sum(len(p.strip()) for p in pages) > 200:
            result = await parse_document([(f"text of pages", c) for c in _chunks(pages)])
        else:  # scanned PDF: one page per request, each with its OCR transcription
            result = await parse_document([(f"page {i + 1} image", b) for i, b in enumerate(pdf_pages_images(path))],
                                          images=True)
    elif ext in IMAGES:
        result = await parse_document([("image", path)], images=True)
    else:
        text = decode(raw)
        if ext in TABULAR and looks_tabular(text):
            result = await parse_tabular(read_delimited(text))
        else:  # MT940, CAMT XML, OFX, QIF, plain text...
            result = await parse_document([("text", c) for c in _chunks([text], 9000)])

    txs = result["transactions"]
    # pick / suggest the account
    meta = result["meta"]
    with db() as c:
        if not account_id and meta.get("account_iban"):
            iban = re.sub(r"\s", "", meta["account_iban"]).upper()
            for a in c.execute("SELECT * FROM accounts").fetchall():
                if a["iban"] and re.sub(r"\s", "", a["iban"]).upper() == iban:
                    account_id = a["id"]
        meta["account_id"] = account_id
        add_fingerprints(txs, account_id)
        if account_id:
            existing = {r["fingerprint"] for r in c.execute(
                "SELECT fingerprint FROM transactions WHERE account_id = ?", (account_id,))}
            for t in txs:
                t["duplicate"] = t["fingerprint"] in existing
    if meta.get("mapping", {}).get("notes"):
        meta["model_notes"] = meta["mapping"]["notes"]
    missing_fx = 0
    for t in txs:
        detect_original(t)
        if not t.get("counterparty"):
            t["counterparty"] = guess_counterparty(t.get("description", ""))
        tx_fx(t)
        missing_fx += t["fx_source"] == "missing"
    if missing_fx:
        result["warnings"].append(f"No exchange rate found for {missing_fx} line(s) - they are imported without a base-currency amount.")
    result["warnings"] += await categorize_rows(txs)
    dups = sum(1 for t in txs if t.get("duplicate"))
    if dups:
        result["warnings"].append(f"{dups} transaction(s) are already in the ledger and will be skipped.")
    return result
