"""Receipt and invoice extraction with the local vision model."""
import re
from datetime import date, timedelta
from pathlib import Path

from .db import categories, get_settings
from .importers import IMAGES, pdf_pages_images, pdf_pages_text
from .llm import LLM
from .ocr import cross_check, image_content
from .skills import owner_name, system_prompt

NUM = ["number", "null"]
STR = ["string", "null"]


def doc_schema() -> dict:
    names = [c["name"] for c in categories()]
    return {
        "type": "object",
        "properties": {
            "document_type": {"type": "string", "enum": ["receipt", "invoice", "credit_note", "other"]},
            "direction": {"type": "string", "enum": ["purchase", "sales", "unknown"]},
            "merchant": {"type": "string"},
            "merchant_tax_id": {"type": STR},
            "buyer_name": {"type": STR},
            "buyer_tax_id": {"type": STR},
            "invoice_number": {"type": STR},
            "date": {"type": STR},
            "due_date": {"type": STR},
            "currency": {"type": "string"},
            "total": {"type": NUM},
            "net": {"type": NUM},
            "vat": {"type": NUM},
            "vat_lines": {"type": "array", "items": {"type": "object", "properties": {
                "rate": {"type": NUM}, "net": {"type": NUM}, "vat": {"type": NUM}, "gross": {"type": NUM}},
                "required": ["rate", "net", "vat", "gross"]}},
            "items": {"type": "array", "items": {"type": "object", "properties": {
                "description": {"type": "string"}, "quantity": {"type": NUM}, "unit_price": {"type": NUM},
                "total": {"type": NUM}}, "required": ["description", "quantity", "unit_price", "total"]}},
            "payment_method": {"type": "string", "enum": ["card", "cash", "transfer", "mobile", "other", "unknown"]},
            "already_paid": {"type": "boolean"},
            "card_last4": {"type": STR},
            "category": {"type": "string", "enum": names},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "notes": {"type": "string"},
        },
        "required": ["document_type", "direction", "merchant", "merchant_tax_id", "buyer_name", "buyer_tax_id",
                     "invoice_number", "date", "due_date", "currency", "total", "net", "vat", "vat_lines", "items",
                     "payment_method", "already_paid", "card_last4", "category", "confidence", "notes"],
    }


TASK = """Read the attached {what} and return the JSON.
- merchant = the seller / issuer of the document.
- direction: 'purchase' when {me} is the buyer - the normal case. 'sales' only when {me} issued the document to a
  customer (merchant is then still the issuer). 'unknown' if you cannot tell.
- total = the final amount paid or payable (gross, after discounts). Cash handed over, change given back and a tip
  written by hand are not part of it.
- net / vat: the amount without VAT and the VAT amount, only if they are printed. vat_lines: one entry per VAT rate,
  rate as a percent number (25 for 25%).
- items: the line items as printed, at most 40. An empty list when there are none or they cannot be read.
- date and due_date as YYYY-MM-DD; currency as ISO code.
- already_paid: true when the document shows the amount has been paid (a till receipt, "betalt", "paid", "payment
  received", charged to a card / PayPal / MobilePay / Klarna); false when it still has to be paid (a due date, bank
  details to pay to).
- notes: at most two short sentences, and only for something the owner must know (an unreadable part, a mixed
  purchase, a payment plan, a difference between the image and the OCR text). Otherwise an empty string.
- Use null for anything that is not printed. Never guess or calculate a missing number."""


def heic_to_jpeg(path: Path) -> Path:
    """iPhone HEIC/HEIF photo -> a JPEG next to it (upright, original size). Returns the JPEG's path."""
    from PIL import Image, ImageOps
    from pillow_heif import register_heif_opener
    register_heif_opener()
    out = path.with_suffix(".jpg")
    with Image.open(path) as img:
        ImageOps.exif_transpose(img).convert("RGB").save(out, "JPEG", quality=92)
    return out


async def prepare_content(path: Path) -> dict:
    """{content, ocr_text, warnings}: a PDF's own text when it has one, else image(s) plus the optional OCR pass."""
    ext = path.suffix.lower()
    if ext in IMAGES:
        return await image_content([path])
    if ext == ".pdf":
        pages = pdf_pages_text(path)
        text = "\n".join(f"--- page {i + 1} ---\n{p}" for i, p in enumerate(pages[:6]))
        if len(text.strip()) > 120:  # digital PDF: its text is exact, no OCR needed
            return {"content": "Text of the document:\n" + text[:20000], "ocr_text": None, "source_text": text[:20000],
                    "warnings": []}
        return await image_content(pdf_pages_images(path, max_pages=3))
    if ext in (".html", ".htm", ".txt"):  # an email whose body is the receipt (no attachment)
        from .mail import html_to_text
        raw = path.read_text(encoding="utf-8", errors="replace")
        text = html_to_text(raw) if ext != ".txt" else raw
        return {"content": "Text of the email:\n" + text[:20000], "ocr_text": None, "source_text": text[:20000],
                "warnings": []}
    if ext in (".heic", ".heif"):        # iPhone photos (e.g. an email attachment): read a JPEG copy
        return await image_content([heic_to_jpeg(path)])
    raise ValueError(f"Unsupported file type {ext}. Use a photo (jpg/png), a PDF, or a scan.")


def sanity_checks(d: dict) -> list[str]:
    w = []
    t, n, v = d.get("total"), d.get("net"), d.get("vat")
    if t is None:
        w.append("No total found - enter it manually.")
    if t is not None and n is not None and v is not None and abs(n + v - t) > 0.02:
        w.append(f"Net {n} + VAT {v} does not equal total {t}.")
    items = [i["total"] for i in d.get("items") or [] if isinstance(i.get("total"), (int, float))]
    if t is not None and items and len(items) == len(d.get("items") or []):
        s = sum(items)
        # line items may be printed gross (receipts) or net (invoices)
        if abs(s - t) > 0.05 and (n is None or abs(s - n) > 0.05):
            w.append(f"Line items add up to {s:.2f}, total says {t:.2f} (discounts or a misread line?).")
    lines = [x.get("vat") for x in d.get("vat_lines") or [] if isinstance(x.get("vat"), (int, float))]
    if v is not None and lines and abs(sum(lines) - v) > 0.02:
        w.append(f"VAT lines add up to {sum(lines):.2f}, VAT total says {v:.2f}.")
    try:
        dt = date.fromisoformat(d.get("date") or "")
        if dt > date.today() + timedelta(days=1):
            w.append("Date is in the future - check day/month order.")
        elif dt < date.today() - timedelta(days=800):
            w.append("Date is more than two years ago - check it.")
    except ValueError:
        w.append("No valid date found.")
    if d.get("confidence") == "low":
        w.append("Model is not confident about this document - please check every field.")
    return w


async def extract_document(path: Path, kind: str = "receipt") -> dict:
    s = get_settings()
    me = owner_name(s) or "the owner of the books"
    skill = "receipt-extraction" if kind == "receipt" else "invoice-management"
    sys = system_prompt(skill, task=TASK.format(what="receipt" if kind == "receipt" else "invoice", me=me))
    prep = await prepare_content(path)
    llm = LLM()

    async def read(effort=None):
        d = await llm.extract(sys, prep["content"], doc_schema(), effort=effort, max_tokens=12000)
        d["currency"] = (d.get("currency") or s["base_currency"]).upper()[:3]
        for k in ("merchant_tax_id", "buyer_tax_id"):  # "CVR-nr. 12345678" -> "12345678"
            if d.get(k):
                d[k] = re.sub(r"^(cvr(-nr)?\.?|se-nr\.?|vat( id| no\.?)?|ust-idnr\.?|moms-?nr\.?)[:\s]*", "", d[k].strip(), flags=re.I)
        if d.get("net") is None and d.get("total") is not None and d.get("vat") is not None:
            d["net"] = round(d["total"] - d["vat"], 2)
        # every amount is read twice: against the OCR transcription of a photo, or against the document's own text
        if prep["ocr_text"]:
            second = cross_check(d, prep["ocr_text"])
        else:
            second = cross_check(d, prep.get("source_text"), "the text of the document", "the document")
        return d, sanity_checks(d) + second

    data, issues = await read()
    if issues and llm.effort_docs != "xhigh":
        # the fast read did not pass the checks: read again, this time letting the model reason carefully
        data2, issues2 = await read("xhigh")
        if len(issues2) <= len(issues):
            data, issues = data2, issues2
        issues.append("Re-read with full reasoning because the first pass did not pass the checks.")
    data["warnings"] = prep["warnings"] + issues
    data["ocr_text"] = prep["ocr_text"]
    return data
