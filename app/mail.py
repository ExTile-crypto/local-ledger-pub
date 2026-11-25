"""Email inboxes: find receipts and invoices in IMAP mailboxes and read them with the local model.

Read-only by design: folders are opened with EXAMINE (readonly) and messages fetched with BODY.PEEK, so nothing is
marked as read, moved or deleted. Each message is processed once (tracked in email_items by Message-ID).

Two steps, so the mailbox scan does not depend on the model being online:
  scan_account()     IMAP -> candidate emails -> attachments / email text saved, items 'pending'
  process_pending()  model confirms it is a purchase document, reads it, saves a receipt or invoice
"""
import asyncio
import email
import hashlib
import imaplib
import logging
import re
import ssl
from datetime import date, datetime, timedelta
from email import policy
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

from .config import UPLOAD_DIR
from .db import db, get_settings, now

log = logging.getLogger("ledger.mail")

PRESETS = {
    "gmail": {"label": "Gmail", "host": "imap.gmail.com", "port": 993,
              "help": "Needs an app password: Google Account > Security > 2-Step Verification > App passwords "
                      "(https://myaccount.google.com/apppasswords)."},
    "icloud": {"label": "iCloud Mail", "host": "imap.mail.me.com", "port": 993,
               "help": "Needs an app-specific password from account.apple.com > Sign-In and Security."},
    "yahoo": {"label": "Yahoo Mail", "host": "imap.mail.yahoo.com", "port": 993,
              "help": "Needs an app password: Yahoo Account Security > Generate app password."},
    "fastmail": {"label": "Fastmail", "host": "imap.fastmail.com", "port": 993,
                 "help": "Needs an app password: Settings > Privacy & Security > App passwords."},
    "other": {"label": "Other (IMAP)", "host": "", "port": 993, "help": "Your provider's IMAP server and password."},
}

# subject / sender words that suggest a receipt, order or bill (Danish, English, German, Swedish, Norwegian)
KEYWORDS = re.compile(
    r"kvittering|faktura|invoice|receipt|ordrebekr|ordre ?nr|ordrenummer|order (confirmation|#|number|no)|your order|"
    r"din ordre|din bestilling|købsbekr|betalingsbekr|betaling (modtaget|gennemført|bekræftet)|"
    r"payment (received|confirmation|receipt|successful)|billing|abonnement|subscription|fornyelse|renewal|"
    r"booking|billet|e-?ticket|rechnung|quittung|bestellbest|kvitto|tak for (dit|din) (køb|ordre|bestilling)|"
    r"thank(s| you) for your (order|purchase|payment)|your purchase|order shipped|"
    r"paragon|potwierdzenie (zamówienia|zamowienia|płatności|platnosci|zakupu)|zamówienie nr|zamowienie nr|rachunek|"
    r"dziękujemy za (zakup|zamówienie|płatność)|płatność (otrzymana|zrealizowana)|bilet|rezerwacj|"
    r"bestellung|zahlungsbestätigung|ihre rechnung|commande|facture|reçu|factura|recibo|fattura|ricevuta", re.I)
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MAX_MESSAGE_BYTES = 25 * 1024 * 1024
MAX_CANDIDATES = 150          # per account per scan; the rest is picked up next time
STATE = {"running": False, "phase": "", "started": None, "finished": None, "last": None}


class MailError(Exception):
    pass


# ---------------------------------------------------------------- email text

class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "table", "section", "article", "hr"}
    SKIP = {"script", "style", "head", "title", "noscript"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in ("td", "th"):
            self.out.append(" | ")
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    p.close()
    lines = [re.sub(r"[ \t ​‌]+", " ", ln).strip(" |") for ln in "".join(p.out).splitlines()]
    return "\n".join(ln for ln in lines if ln)


def message_text(msg) -> str:
    body = msg.get_body(preferencelist=("html", "plain"))
    if body is None:
        return ""
    try:
        content = body.get_content()
    except (LookupError, ValueError):
        content = body.get_payload(decode=True).decode("latin-1", "replace")
    return html_to_text(content) if body.get_content_type() == "text/html" else content.strip()


# ---------------------------------------------------------------- IMAP

def _quote(folder: str) -> str:
    return folder if folder.startswith('"') else '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'


def connect(acc: dict) -> imaplib.IMAP4:
    host, port, sec = acc["host"], int(acc["port"] or 993), acc.get("security") or "ssl"
    ctx = ssl.create_default_context()
    try:
        if sec == "ssl":
            m = imaplib.IMAP4_SSL(host, port, ssl_context=ctx, timeout=30)
        else:
            m = imaplib.IMAP4(host, port, timeout=30)
            if sec == "starttls":
                m.starttls(ssl_context=ctx)
    except (OSError, ssl.SSLError) as e:
        raise MailError(f"Cannot connect to {host}:{port} ({e})")
    try:
        m.login(acc["username"], acc["password"] or "")
    except imaplib.IMAP4.error as e:
        raise MailError(f"Login refused for {acc['username']}: {e.args[0].decode() if e.args and isinstance(e.args[0], bytes) else e}. "
                        "Gmail, iCloud and Yahoo need an app password, not your normal password.")
    return m


def list_folders(acc: dict) -> list[str]:
    m = connect(acc)
    try:
        typ, data = m.list()
        names = []
        for line in data or []:
            if isinstance(line, bytes):
                mm = re.match(rb'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$', line)
                if mm and b"\\Noselect" not in mm.group("flags"):
                    names.append(mm.group("name").decode("utf-8", "replace").strip('"'))
        return names
    finally:
        try:
            m.logout()
        except Exception:
            pass


def _parse_fetch(data) -> list[tuple[int, int, bytes]]:
    """(uid, size, header bytes) from a FETCH (UID RFC822.SIZE BODY.PEEK[HEADER.FIELDS ...]) response."""
    out = []
    for entry in data or []:
        if isinstance(entry, tuple) and len(entry) >= 2:
            meta = entry[0]
            u = re.search(rb"UID (\d+)", meta)
            s = re.search(rb"RFC822\.SIZE (\d+)", meta)
            if u:
                out.append((int(u.group(1)), int(s.group(1)) if s else 0, entry[1]))
    return out


def _known(c, message_id: str) -> bool:
    return bool(c.execute("SELECT 1 FROM email_items WHERE message_id = ? LIMIT 1", (message_id,)).fetchone())


def _save(account_id: int, message_id: str, name: str, data: bytes) -> Path:
    folder = UPLOAD_DIR / "email" / datetime.now().strftime("%Y-%m")
    folder.mkdir(parents=True, exist_ok=True)
    tag = hashlib.sha1(f"{account_id}{message_id}".encode()).hexdigest()[:10]
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)[-80:] or "file"
    path = folder / f"{tag}_{safe}"
    path.write_bytes(data)
    return path


def _add_item(c, acc_id, folder, mid, part, meta, kind, path, status="pending", reason=""):
    c.execute(
        "INSERT OR IGNORE INTO email_items(account_id, folder, message_id, part, sent_at, sender, subject, kind, file_path, "
        "eml_path, status, reason, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (acc_id, folder, mid, part, meta["date"], meta["sender"], meta["subject"], kind,
         str(path.relative_to(UPLOAD_DIR)) if path else None, meta.get("eml"), status, reason, now()))


def scan_account(acc: dict) -> dict:
    """Find new receipt-like emails in one account and store their documents as pending items."""
    stats = {"account": acc["name"], "checked": 0, "candidates": 0, "new_items": 0, "errors": []}
    if acc.get("last_scan_at"):
        since = date.fromisoformat(acc["last_scan_at"][:10]) - timedelta(days=3)   # overlap: late-arriving mail
    else:
        since = date.today() - timedelta(days=int(acc.get("since_days") or 60))
    since_str = f"{since.day:02d}-{MONTHS[since.month - 1]}-{since.year}"
    m = connect(acc)
    try:
        for folder in [f.strip() for f in (acc.get("folders") or "INBOX").split(",") if f.strip()]:
            typ, _ = m.select(_quote(folder), readonly=True)          # EXAMINE: nothing gets marked as read
            if typ != "OK":
                stats["errors"].append(f"Folder not found: {folder}")
                continue
            typ, data = m.uid("SEARCH", None, "SINCE", since_str)
            uids = (data[0] or b"").split() if data else []
            heads = []
            for i in range(0, len(uids), 200):
                typ, data = m.uid("FETCH", b",".join(uids[i:i + 200]),
                                  "(UID RFC822.SIZE BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM SUBJECT DATE CONTENT-TYPE)])")
                heads += _parse_fetch(data)
            stats["checked"] += len(heads)
            picked = 0
            for uid, size, raw in sorted(heads, key=lambda h: -h[0]):           # newest first
                if picked >= MAX_CANDIDATES:
                    break
                h = email.message_from_bytes(raw, policy=policy.default)
                subject, sender = str(h.get("Subject", "")), str(h.get("From", ""))
                ctype = str(h.get("Content-Type", "")).lower()
                mid = str(h.get("Message-ID", "")).strip() or f"<uid{uid}.{folder}.{acc['id']}>"
                keyword = bool(KEYWORDS.search(subject) or KEYWORDS.search(parseaddr(sender)[1].split("@")[0]))
                if not keyword and "multipart/mixed" not in ctype:
                    continue                                                    # no receipt words, no attachments
                if size > MAX_MESSAGE_BYTES:
                    continue
                with db() as c:
                    if _known(c, mid):
                        continue
                picked += 1
                typ, data = m.uid("FETCH", str(uid).encode(), "(BODY.PEEK[])")
                full = next((e[1] for e in data or [] if isinstance(e, tuple)), None)
                if not full:
                    continue
                msg = email.message_from_bytes(full, policy=policy.default)
                try:
                    sent = parsedate_to_datetime(str(msg.get("Date"))).date().isoformat()
                except (TypeError, ValueError):
                    sent = None
                meta = {"date": sent, "sender": sender[:200], "subject": subject[:300]}
                docs = []
                for part in msg.iter_attachments():
                    ct, fn = part.get_content_type(), part.get_filename() or ""
                    payload = part.get_payload(decode=True) or b""
                    if ct == "application/pdf" or fn.lower().endswith(".pdf"):
                        docs.append(("pdf", fn or "attachment.pdf", payload))
                    elif ct in ("image/jpeg", "image/png", "image/webp") and part.get_content_disposition() == "attachment" \
                            and len(payload) > 8_000:   # skip tiny icons
                        docs.append(("image", fn or "attachment.jpg", payload))
                with db() as c:
                    if not docs and not keyword:
                        _add_item(c, acc["id"], folder, mid, "-", meta, "none", None, "ignored", "no receipt words, no PDF")
                        continue
                    meta["eml"] = str(_save(acc["id"], mid, "message.eml", full).relative_to(UPLOAD_DIR))
                    stats["candidates"] += 1
                    if docs:
                        for kind, fn, payload in docs:
                            _add_item(c, acc["id"], folder, mid, fn, meta, kind, _save(acc["id"], mid, fn, payload))
                            stats["new_items"] += 1
                    else:                                                       # the email itself is the receipt
                        text = f"From: {sender}\nSubject: {subject}\nDate: {msg.get('Date')}\n\n{message_text(msg)}"
                        _add_item(c, acc["id"], folder, mid, "body", meta, "body",
                                  _save(acc["id"], mid, "email.txt", text.encode("utf-8")))
                        stats["new_items"] += 1
    finally:
        try:
            m.logout()
        except Exception:
            pass
    return stats


# ---------------------------------------------------------------- reading with the model

CLASSIFY = {
    "type": "object",
    "properties": {"is_purchase_document": {"type": "boolean"},
                   "document_type": {"type": "string", "enum": ["receipt", "invoice", "other"]},
                   "reason": {"type": "string"}},
    "required": ["is_purchase_document", "document_type", "reason"],
}
CLASSIFY_PROMPT = """You sort emails for a private bookkeeping ledger. Decide whether this email (or its attachment) is itself the
document for money paid or owed:
- receipt: a receipt, payment confirmation or order confirmation that shows the amount paid (shops, apps,
  subscriptions, tickets, travel, food delivery).
- invoice: an invoice or bill that has to be paid, or was paid by bank transfer (faktura, Rechnung, invoice).
- other: everything else, even when it repeats order lines and amounts -
  shipping, pickup and delivery notices; review or rating requests ("bedøm", "anmeld", "rate your purchase");
  "thank you" follow-ups; payment reminders without the document; credit agreements, payment-plan terms and
  "payment information" (Klarna, ViaBill); terms and conditions; license keys; insurance certificates; card,
  account or balance notices; statements of account; newsletters, offers and ads; login or password emails;
  personal mail.
Be strict: only the purchase document itself counts (a receipt, an order confirmation with the amount, an invoice).
Answer in JSON; reason = one short sentence."""


def _preview(item: dict, path: Path) -> str:
    head = f"From: {item['sender']}\nSubject: {item['subject']}\nAttachment: {item['part']}\n\n"
    if item["kind"] == "pdf":
        from .importers import pdf_pages_text
        try:
            return head + "\n".join(pdf_pages_text(path)[:2])[:3000]
        except Exception:
            return head
    if item["kind"] == "body":
        return head + path.read_text(encoding="utf-8", errors="replace")[:3000]
    return head


def _duplicate(c, d: dict) -> str | None:
    """The same purchase often arrives as several emails (order confirmation, invoice PDF, receipt): keep the first."""
    from .db import loads
    from .reconcile import name_similarity
    if d.get("total") is None:
        return None
    num = (d.get("invoice_number") or "").strip()
    if len(num) >= 4:
        # same number AND same amount: installment plans (Klarna, ViaBill...) send one email per payment, all with
        # the order's number - each payment is its own receipt
        r = c.execute("SELECT id FROM invoices WHERE number = ? AND abs(total - ?) < 0.011", (num, d["total"])).fetchone()
        if r:
            return f"same document already saved (invoice #{r['id']})"
        for r in c.execute("SELECT id, raw, total FROM receipts WHERE raw LIKE ?", (f"%{num}%",)):
            if (loads(r["raw"], {}).get("invoice_number") or "").strip() == num and r["total"] is not None \
                    and abs(r["total"] - d["total"]) < 0.011:
                return f"same document already saved (receipt #{r['id']})"
    if d.get("date"):   # same amount and currency within a day: one purchase (shop vs. platform / merchant of record)
        for table, dcol in (("receipts", "date"), ("invoices", "issue_date")):
            r = c.execute(f"SELECT id FROM {table} WHERE abs(total - ?) < 0.011 AND upper(currency) = upper(?) "
                          f"AND abs(julianday({dcol}) - julianday(?)) <= 1", (d["total"], d.get("currency") or "", d["date"])).fetchone()
            if r:
                return f"same purchase already saved ({table[:-1]} #{r['id']})"
    if d.get("date"):   # same amount and currency within two weeks from the same seller
        for table, dcol, ncol in (("receipts", "date", "merchant"), ("invoices", "issue_date", "counterparty")):
            for r in c.execute(f"SELECT id, {ncol} AS name FROM {table} WHERE abs(total - ?) < 0.011 AND upper(currency) = upper(?) "
                               f"AND abs(julianday({dcol}) - julianday(?)) <= 14", (d["total"], d.get("currency") or "", d["date"])):
                if name_similarity(d.get("merchant") or "", r["name"] or ""):
                    return f"same purchase already saved ({table[:-1]} #{r['id']})"
    return None


async def process_pending(limit: int = 40) -> dict:
    """Read pending email documents with the model. Stops quietly when the model is offline."""
    from . import store
    from .documents import extract_document
    from .llm import LLM, LLMError, LLMOffline, LLMWrongModel
    from .reconcile import auto_match
    stats = {"imported": 0, "skipped": 0, "errors": 0, "waiting": 0}
    with db() as c:
        items = c.execute("SELECT * FROM email_items WHERE status = 'pending' ORDER BY sent_at, id LIMIT ?", (limit,)).fetchall()
    llm = LLM()
    for it in items:
        STATE["phase"] = f"Reading: {it['subject'][:60]}"
        path = UPLOAD_DIR / it["file_path"]
        try:
            kind = "receipt"
            if it["kind"] != "image":
                cls = await llm.extract(CLASSIFY_PROMPT, _preview(it, path), CLASSIFY, thinking=False, max_tokens=400)
                if not cls.get("is_purchase_document") or cls.get("document_type") == "other":
                    _finish(it["id"], "skipped", reason="not a receipt or invoice: " + (cls.get("reason") or "")[:200])
                    stats["skipped"] += 1
                    continue
                kind = "invoice" if cls.get("document_type") == "invoice" else "receipt"
            d = await extract_document(path, kind)
            # anything already paid (card, PayPal, MobilePay...) is a receipt, so it gets matched to the payment;
            # only bills still to be paid go to Invoices
            paid = d.get("already_paid") or (d.get("payment_method") in ("card", "mobile", "cash", "other"))
            doc_type = "invoice" if (d.get("document_type") in ("invoice", "credit_note") or kind == "invoice") and not paid else "receipt"
            if d.get("document_type") == "other" or d.get("total") is None or abs(d["total"]) < 0.01:
                _finish(it["id"], "skipped", reason="no amount found in the document" if d.get("total") is None else "amount is 0")
                stats["skipped"] += 1
                continue
            with db() as c:
                dup = _duplicate(c, d)
            if dup:
                _finish(it["id"], "skipped", reason=dup)
                stats["skipped"] += 1
                continue
            name = it["part"] if it["part"] not in ("body", "-") else (it["subject"][:60] or "email") + ".txt"
            note = f"From email: {parseaddr(it['sender'])[1] or it['sender']} - \"{it['subject'][:120]}\"."
            if doc_type == "invoice":
                doc_id = store.save_invoice(path, name, d, notes_prefix=note)
            else:
                doc_id, _ = store.save_receipt(path, name, d, notes_prefix=note)
            _finish(it["id"], "imported", doc_type=doc_type, doc_id=doc_id, reason="; ".join(d.get("warnings") or [])[:300])
            stats["imported"] += 1
        except LLMWrongModel as e:     # a text-only model is loaded: this scan/photo waits, text documents go on
            stats["waiting"] += 1
            STATE["phase"] = f"Waiting for a vision model: {it['subject'][:40]}"
            log.info("email item %s waits: %s", it["id"], e)
        except LLMOffline as e:        # model offline / no key: leave everything pending for the next run
            stats["waiting"] = len(items) - stats["imported"] - stats["skipped"] - stats["errors"]
            STATE["phase"] = f"Waiting for the model: {e}"
            break
        except LLMError as e:          # the model refused THIS document (too long, bad output): don't block the rest
            log.warning("email item %s failed: %s", it["id"], e)
            _finish(it["id"], "error", reason=str(e)[:300])
            stats["errors"] += 1
        except Exception as e:
            log.exception("email item %s failed", it["id"])
            _finish(it["id"], "error", reason=f"{type(e).__name__}: {e}"[:300])
            stats["errors"] += 1
    if stats["imported"]:
        auto_match()
    return stats


async def read_all_pending() -> dict:
    """process_pending until nothing is left or the model goes away (up to 400 documents)."""
    total = {"imported": 0, "skipped": 0, "errors": 0, "waiting": 0}
    for _ in range(10):
        st = await process_pending()
        for k in total:
            total[k] += st[k]
        if st["waiting"] or not (st["imported"] or st["skipped"] or st["errors"]):
            break
    return total


def _finish(item_id: int, status: str, doc_type: str = None, doc_id: int = None, reason: str = ""):
    with db() as c:
        c.execute("UPDATE email_items SET status = ?, doc_type = ?, doc_id = ?, reason = ?, processed_at = ? WHERE id = ?",
                  (status, doc_type, doc_id, reason, now(), item_id))


# ---------------------------------------------------------------- running scans

def accounts(enabled_only=False) -> list[dict]:
    with db() as c:
        return c.execute("SELECT * FROM email_accounts" + (" WHERE enabled = 1" if enabled_only else "") + " ORDER BY id").fetchall()


async def run(account_id: int | None = None, read: bool = True) -> dict:
    """Scan the enabled accounts (or one), then read what was found (read=False: scan only). One run at a time."""
    if STATE["running"]:
        return {"busy": True}
    STATE.update(running=True, started=now(), finished=None, phase="Connecting")
    result = {"accounts": [], "processed": None}
    try:
        for acc in accounts(enabled_only=account_id is None):
            if account_id and acc["id"] != account_id:
                continue
            STATE["phase"] = f"Scanning {acc['name']}"
            try:
                st = await asyncio.to_thread(scan_account, acc)
                status = f"ok - {st['checked']} emails checked, {st['new_items']} new document(s)" + \
                         (f"; {'; '.join(st['errors'])}" if st["errors"] else "")
                with db() as c:
                    c.execute("UPDATE email_accounts SET last_scan_at = ?, last_status = ? WHERE id = ?", (now(), status, acc["id"]))
                result["accounts"].append(st)
            except Exception as e:
                msg = str(e) if isinstance(e, MailError) else f"{type(e).__name__}: {e}"
                with db() as c:
                    c.execute("UPDATE email_accounts SET last_status = ? WHERE id = ?", ("error - " + msg[:300], acc["id"]))
                result["accounts"].append({"account": acc["name"], "error": msg})
        if read:
            STATE["phase"] = "Reading documents with the model"
            result["processed"] = await read_all_pending()
    finally:
        STATE.update(running=False, finished=now(), phase="", last=result)
    return result


async def _model_answers() -> bool:
    """Quick check before the periodic retry, so an offline model does not show up as 'Reading...' every ten minutes."""
    from .llm import LLM, LLMError
    try:
        await LLM().status(connect=2.0)
        return True
    except LLMError:
        return False


async def scheduler():
    """Background loop in the web app: scan every `email_scan_hours`, and retry waiting documents when the model is back."""
    await asyncio.sleep(60)
    while True:
        try:
            hours = float(get_settings().get("email_scan_hours") or 0)
            due = False
            if hours > 0:
                for acc in accounts(enabled_only=True):
                    last = acc["last_scan_at"]
                    if not last or (datetime.now() - datetime.fromisoformat(last)).total_seconds() > hours * 3600:
                        due = True
            with db() as c:
                pending = c.execute("SELECT COUNT(*) AS n FROM email_items WHERE status = 'pending'").fetchone()["n"]
            if due:
                await run()
            elif pending and not STATE["running"] and await _model_answers():
                STATE.update(running=True, phase="Reading documents with the model")
                try:
                    await process_pending()
                finally:
                    STATE.update(running=False, phase="", finished=now())
        except Exception:
            log.exception("email scheduler")
        await asyncio.sleep(600)


def counts() -> dict:
    with db() as c:
        rows = c.execute("SELECT status, COUNT(*) AS n FROM email_items WHERE status != 'ignored' GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}
