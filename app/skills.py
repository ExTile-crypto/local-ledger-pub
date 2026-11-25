"""Loads skills/<name>/SKILL.md files and builds system prompts ("preprompts") from them.

A skill is written once and used in two ways:
  mode="extract"  the app asks the model to read something and answer with JSON (receipts, invoices, statements,
                  categorization). No tools, no conversation.
  mode="chat"     the Assistant tab and Unsloth Studio: a conversation with tools.
Sections whose heading ends with "(chat)" are only sent in chat mode, so reading tasks are not told to "call
save_receipt" or "answer with a markdown table"; sections ending with "(reading)" are only sent to reading tasks
(long reference lists the chat does not need). Everything else is shared.

The skills are country-neutral. What a country calls things lives in a country skill (skills/country-<id>/SKILL.md,
see app/packs.py): its section "Receipts" is added after receipt-extraction, "Bank statements" after
bank-statement-import, and so on.

A skill edited in the app is saved under <data>/skills and wins over the built-in file, so updating the app never
overwrites someone's own wording - and "Reset" simply removes that copy.
"""
import re
from datetime import date

from .config import SKILLS_DIR, USER_SKILLS_DIR
from .db import categories, db, get_settings
from .packs import pack, pack_id

# which section(s) of the country skill go with which skill
COUNTRY_SECTIONS = {
    "bookkeeper-core": ("Basics",), "receipt-extraction": ("Receipts",), "invoice-management": ("Invoices",),
    "bank-statement-import": ("Bank statements",), "transaction-categorization": ("Categorization", "Categorization payees"),
    "reconciliation": ("Reconciliation",), "vat-and-tax": ("VAT and tax",),
}

FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.S)
CHAT_ONLY = re.compile(r"\s*\(chat\)\s*$", re.I)
READ_ONLY = re.compile(r"\s*\(reading\)\s*$", re.I)

ROLE = {
    "extract": ("You read financial documents and bank data for a private bookkeeping ledger. Follow the rules below "
                "and answer with exactly the JSON that is asked for - no explanations outside the JSON."),
    "chat": ("You are the bookkeeping assistant inside the owner's private ledger app. You work through the ledger "
             "tools and follow the rules below."),
}

BOOKS = {
    "private": "one person's or household's private finances. There is no business: no sales, no moms return. "
               "Money in is salary, public benefits, refunds or transfers; most spending is private consumption.",
    "business": "a business. Customer payments are sales revenue, purchases need a bilag (receipt or invoice), "
                "and moms is tracked for the VAT return.",
    "mixed": "a private person's finances, possibly with some self-employed or side income. Treat a payment as private "
             "household spending unless it clearly belongs to a business; do not assume money in from a person or a "
             "shop is sales revenue.",
}
KIND_HEAD = [
    ("income", "Income (money in)"),
    ("expense", "Expenses (money out; a refund is money in with the same expense category)"),
    ("transfer", "Transfers (money moved - not income, not an expense)"),
    ("other", "Other"),
]


def _parse(text: str) -> tuple[dict, str]:
    m = FRONT.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"')
    return meta, m.group(2).strip()


def _file(name: str):
    """The file a skill is read from: the user's edited copy if there is one, else the built-in one."""
    user = USER_SKILLS_DIR / name / "SKILL.md"
    return user if user.exists() else SKILLS_DIR / name / "SKILL.md"


def list_skills() -> list[dict]:
    names = sorted({f.parent.name for d in (SKILLS_DIR, USER_SKILLS_DIR) for f in d.glob("*/SKILL.md")})
    out = []
    for n in names:
        meta, body = _parse(_file(n).read_text(encoding="utf-8"))
        out.append({"name": meta.get("name", n), "description": meta.get("description", ""), "folder": n, "body": body,
                    "edited": (USER_SKILLS_DIR / n / "SKILL.md").exists(),
                    "builtin": (SKILLS_DIR / n / "SKILL.md").exists()})
    return out


def read_skill(name: str) -> str:
    f = _file(name)
    return f.read_text(encoding="utf-8") if f.exists() else ""


def write_skill(name: str, text: str):
    if not re.fullmatch(r"[a-z0-9-]+", name):
        raise ValueError("bad skill name")
    if not (SKILLS_DIR / name / "SKILL.md").exists() and not name.startswith("country-"):
        raise FileNotFoundError(name)   # only existing skills, or a new country skill
    f = USER_SKILLS_DIR / name / "SKILL.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")


def reset_skill(name: str) -> bool:
    """Drop the edited copy, so the built-in text is used again."""
    f = USER_SKILLS_DIR / name / "SKILL.md"
    if re.fullmatch(r"[a-z0-9-]+", name) and f.exists():
        f.unlink()
        return True
    return False


def skill_body(name: str, mode: str = "chat") -> str:
    """The skill text for one mode: '(chat)' sections only for mode='chat', '(reading)' sections only for
    mode='extract'. The markers themselves are never sent."""
    body = _parse(read_skill(name))[1]
    out, keep = [], True
    for line in body.splitlines():
        if line.startswith("## "):
            keep = not ((mode != "chat" and CHAT_ONLY.search(line)) or (mode == "chat" and READ_ONLY.search(line)))
            line = READ_ONLY.sub("", CHAT_ONLY.sub("", line))
        if keep:
            out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def country_part(skill: str, mode: str = "extract") -> str:
    """The local vocabulary for one skill, from the country skill of these books ('' when there is none)."""
    p = pack()
    wanted = COUNTRY_SECTIONS.get(skill)
    if not p["skill"] or not wanted:
        return ""
    sections, cur = {}, None
    for line in skill_body(p["skill"], mode).splitlines():
        if line.startswith("## "):
            cur = line[3:].strip()
            sections[cur] = []
        elif cur is not None:
            sections[cur].append(line)
    text = "\n".join("\n".join(sections[w]).strip() for w in wanted if sections.get(w)).strip()
    return f"## In {p['name']}\n{text}" if text else ""


def skill_with_country(name: str, mode: str) -> str:
    return "\n\n".join(x for x in (skill_body(name, mode), country_part(name, mode)) if x)


def owner_name(s: dict | None = None) -> str:
    s = s or get_settings()
    return (s.get("company_name") or "").strip() or (s.get("owner_names") or "").split(",")[0].strip()


def ledger_context() -> str:
    s = get_settings()
    cats = categories()
    kind = s.get("books_type") or "mixed"
    lines = [f"- Today: {date.today().isoformat()} ({date.today():%A})",
             f"- The books are {BOOKS.get(kind, BOOKS['mixed'])}"]
    who = owner_name(s)
    if who or s.get("country"):
        lines.append("- Owner: " + (who or "not named") + (f", {s['country']}" if s.get("country") else ""))
    if pack_id(s) == "generic":
        lines.append("- No country notes are installed for these books: go by the document's own language and the "
                     "conventions of its country.")
    names = ", ".join(n.strip() for n in (s.get("owner_names") or "").split(",") if n.strip())
    if names:
        lines.append(f"- The owner appears on bank lines as: {names}. A payment to or from that name is a transfer "
                     "between the owner's own accounts.")
    lines.append(f"- Base currency: {s['base_currency']}")
    if kind != "private":
        lines.append(f"- VAT registered: {'yes' if s['vat_registered'] == '1' else 'no'}")
    with db(readonly=True) as c:
        accounts = c.execute("SELECT name, kind, currency FROM accounts ORDER BY kind, name").fetchall()
    if accounts:
        lines.append("- Accounts: " + ", ".join(
            f"{a['name']} ({'cash book' if a['kind'] == 'cash' else 'bank'}{', ' + a['currency'] if a['currency'] else ''})"
            for a in accounts))
    out = ["## These books", *lines, "", "## Categories (use these exact names)"]
    for k, head in KIND_HEAD:
        group = [c for c in cats if c["kind"] == k]
        if group:
            out.append(head + ":")
            out += [f"- {c['name']}" + (f": {c['description']}" if c["description"] else "") for c in group]
    return "\n".join(out)


def ledger_snapshot() -> str:
    """What is in the ledger right now - lets the Assistant know which periods it can answer for and what is open."""
    with db(readonly=True) as c:
        accs = c.execute(
            "SELECT a.name, a.kind, COUNT(t.id) AS n, MIN(t.date) AS first, MAX(t.date) AS last FROM accounts a "
            "LEFT JOIN transactions t ON t.account_id = a.id GROUP BY a.id ORDER BY a.kind, a.name").fetchall()
        one = lambda q: c.execute(q).fetchone()["n"]  # noqa: E731
        uncategorized = one("SELECT COUNT(*) AS n FROM transactions WHERE category = 'Uncategorized'")
        review = one("SELECT COUNT(*) AS n FROM receipts WHERE status = 'review'")
        receipts = one("SELECT COUNT(*) AS n FROM receipts")
        unmatched = one("SELECT COUNT(*) AS n FROM receipts WHERE transaction_id IS NULL")
        unpaid = one("SELECT COUNT(*) AS n FROM invoices WHERE status = 'unpaid'")
        rules = one("SELECT COUNT(*) AS n FROM rules")
    lines = ["## In the ledger now"]
    for a in accs:
        cover = f"{a['n']} transactions, {a['first']} to {a['last']}" if a["n"] else "no transactions yet"
        lines.append(f"- {a['name']}: {cover}")
    lines.append(f"- {uncategorized} uncategorized transactions; {receipts} receipts ({review} to review, "
                 f"{unmatched} without a bank line); {unpaid} unpaid invoices; {rules} learned rules")
    lines.append("- A period after an account's last date has no data yet: say that the statement is not imported, "
                 "do not report it as zero spending.")
    return "\n".join(lines)


def system_prompt(*skills: str, task: str = "", context: bool = True, mode: str = "extract",
                  snapshot: bool = False) -> str:
    """Role, core rules, the task's skills, the books' context, then the task itself.
    The stable parts come first so the model server can reuse its cache from one document to the next."""
    parts = [ROLE.get(mode, ROLE["extract"]), skill_with_country("bookkeeper-core", mode)]
    parts += [skill_with_country(s, mode) for s in skills if s != "bookkeeper-core"]
    if context:
        parts.append(ledger_context())
    if snapshot:
        parts.append(ledger_snapshot())
    if task:
        parts.append("## Current task\n" + task.strip())
    return "\n\n".join(p for p in parts if p)
