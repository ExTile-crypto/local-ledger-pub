"""Assign categories: learned rules first, then category keywords, then what the owner chose before for the same
payee, then the model for the rest.

The model is asked once per PAYEE, not once per line: 58 payments to the same shop are one question with one answer.
That keeps a payee in one category (line-by-line batches gave the same shop different categories), and it is several
times fewer tokens for a long statement.
"""
import re
from collections import Counter, defaultdict

from .db import categories, db, now
from .llm import LLM, LLMError
from .skills import system_prompt

BATCH = 40          # payees per model request
WEAK = ("Uncategorized", "Other expenses", "Other income")   # not worth copying as a precedent


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _sign_ok(kind: str, amount) -> bool:
    if amount is None:
        return True
    if kind == "income":
        return amount > 0
    if kind == "expense":
        return amount < 0
    return True


def rule_match(row: dict, rules: list[dict], cats: list[dict]):
    text = _norm(f"{row.get('counterparty', '')} {row.get('description', '')}")
    kinds = {c["name"]: c["kind"] for c in cats}
    for r in rules:  # user-taught rules win
        if r["pattern"].lower() in text and _sign_ok(kinds.get(r["category"], "other"), row.get("amount")):
            return r["category"], "rule"
    for c in cats:
        if not _sign_ok(c["kind"], row.get("amount")):
            continue
        for kw in [k.strip().lower() for k in (c["keywords"] or "").split(",") if k.strip()]:
            if re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", text):
                return c["name"], "keyword"
    return None, None


def payee_key(row: dict) -> tuple[str, str]:
    """(payee, direction): what makes two lines 'the same payee paid the same way'.
    Long digit runs (terminal ids, references, dates) are dropped so 'NETTO 4521' and 'NETTO 4733' are one payee."""
    text = _norm(row.get("counterparty") or "") or _norm(row.get("description") or "")
    text = re.sub(r"\d{4,}", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -*.,#")
    return text[:60], "out" if (row.get("amount") or 0) < 0 else "in"


def _history(c) -> dict:
    """payee key -> {'user': Counter, 'all': Counter} of how its lines are categorized in the ledger today."""
    hist = defaultdict(lambda: {"user": Counter(), "all": Counter()})
    for t in c.execute("SELECT counterparty, description, amount, category, category_source FROM transactions "
                       "WHERE category != 'Uncategorized'"):
        h = hist[payee_key(t)]
        if t["category_source"] == "user":
            h["user"][t["category"]] += 1
        if t["category"] not in WEAK:
            h["all"][t["category"]] += 1
    return hist


async def categorize_rows(rows: list[dict], use_llm: bool = True) -> list[str]:
    """Fill row['category'] / row['category_source'] in place. Returns warnings."""
    cats = categories()
    names = [c["name"] for c in cats]
    kinds = {c["name"]: c["kind"] for c in cats}
    with db() as c:
        rules = c.execute("SELECT pattern, category FROM rules ORDER BY length(pattern) DESC").fetchall()
        hist = _history(c)
    groups = defaultdict(list)
    for r in rows:
        cat, src = rule_match(r, rules, cats)
        if not cat:
            # the owner already chose a category for this payee, at least twice and (almost) always the same one
            user = hist[payee_key(r)]["user"] if payee_key(r)[0] else Counter()
            if user:
                top, n = user.most_common(1)[0]
                if n >= 2 and n >= 0.6 * sum(user.values()) and top in kinds and _sign_ok(kinds[top], r.get("amount")):
                    cat, src = top, "history"
        if cat:
            r["category"], r["category_source"] = cat, src
        else:
            r["category"], r["category_source"] = "Uncategorized", ""
            groups[payee_key(r)].append(r)
    if not use_llm or not groups:
        return []
    schema = {
        "type": "object",
        "properties": {"results": {"type": "array", "items": {
            "type": "object",
            "properties": {"i": {"type": "integer"}, "category": {"type": "string", "enum": names}},
            "required": ["i", "category"]}}},
        "required": ["results"],
    }
    task = ("Categorize every numbered payee below; return exactly one result per number. Each line is one payee with "
            "all its payments in one direction: n = how many payments, amount = a typical one.\n"
            "- direction OUT = money paid out: an expense or a transfer category, never an income category.\n"
            "- direction IN = money received: income, a refund (the same expense category as the purchase) or a transfer.\n"
            "- 'before: X' = how this payee is already categorized in the ledger; keep it unless it is clearly wrong here.\n"
            "- 'Uncategorized' only when you truly cannot tell what the payment is.")
    sys = system_prompt("transaction-categorization", task=task)
    llm = LLM()
    warnings = []
    todo = sorted(groups.items(), key=lambda kv: -len(kv[1]))   # the payees with most lines first
    for start in range(0, len(todo), BATCH):
        batch = todo[start:start + BATCH]
        lines = []
        for i, ((_, direction), members) in enumerate(batch):
            ex = max(members, key=lambda r: len(r.get("description") or ""))
            amounts = sorted(abs(r.get("amount") or 0) for r in members)
            typical = amounts[len(amounts) // 2]
            # an earlier choice is only worth repeating when it is established: 3+ lines and a clear majority
            seen = hist[payee_key(ex)]["all"]
            before = [(k, n) for k, n in seen.most_common(1) if n >= 3 and n >= 0.6 * sum(seen.values())]
            cp = (ex.get("counterparty") or "")[:60]
            desc = (ex.get("description") or "")[:160]
            lines.append(" | ".join([
                str(i), direction.upper(), f"n={len(members)}",
                f"{'-' if direction == 'out' else '+'}{typical:.2f} {ex.get('currency') or ''}".strip(),
                cp or "-", desc if _norm(desc) != _norm(cp) else "-",
                "before: " + ", ".join(f"{k} x{n}" for k, n in before) if before else "-"]))
        try:
            out = await llm.extract(sys, "i | direction | payments | amount | counterparty | text | before\n" + "\n".join(lines),
                                    schema, thinking=False, max_tokens=4000)
        except (LLMError, ValueError) as e:
            warnings.append(f"Model categorization failed: {e}")
            break
        for res in out.get("results", []):
            i, cat = res.get("i"), res.get("category")
            if not (isinstance(i, int) and 0 <= i < len(batch) and cat in kinds) or cat == "Uncategorized":
                continue
            for r in batch[i][1]:
                if not _sign_ok(kinds[cat], r.get("amount")) and kinds[cat] == "income":
                    continue  # money out can't be income - leave it for review rather than book it wrong
                r["category"], r["category_source"] = cat, "llm"
    return warnings


async def recategorize(scope: str = "uncategorized", use_llm: bool = True, limit: int = 1500) -> dict:
    """Categorize what is still open. scope 'weak' also re-checks lines that only got a catch-all category from the
    model (Other expenses / Other income). A category the owner chose is never touched."""
    where = "category = 'Uncategorized'"
    if scope == "weak":
        where = ("(category = 'Uncategorized' OR (category IN ('Other expenses', 'Other income') "
                 "AND category_source IN ('', 'llm')))")
    with db() as c:
        rows = c.execute("SELECT id, date, amount, currency, description, counterparty, category AS old FROM transactions "
                         f"WHERE {where} ORDER BY date DESC LIMIT ?", (limit,)).fetchall()
    warnings = await categorize_rows(rows, use_llm=use_llm)
    changed = [r for r in rows if r["category"] not in ("Uncategorized", r["old"])]
    with db() as c:
        c.executemany("UPDATE transactions SET category = ?, category_source = ? WHERE id = ?",
                      [(r["category"], r["category_source"], r["id"]) for r in changed])
        left = c.execute("SELECT COUNT(*) AS n FROM transactions WHERE category = 'Uncategorized'").fetchone()["n"]
    return {"checked": len(rows), "categorized": len(changed), "still_uncategorized": left,
            "by_category": dict(Counter(r["category"] for r in changed).most_common()),
            "how": dict(Counter(r["category_source"] for r in changed)), "warnings": warnings}


def learn_rule(counterparty: str, description: str, category: str) -> str | None:
    """Remember a user correction as a substring rule (counterparty preferred)."""
    pattern = _norm(counterparty)
    if len(pattern) < 3:
        words = [w for w in re.findall(r"[a-zæøåąćęłńóśźżäöüß]{3,}", _norm(description)) if not w.isdigit()]
        pattern = " ".join(words[:3])
    if len(pattern) < 3:
        return None
    with db() as c:
        c.execute("INSERT INTO rules(pattern, category, created_at) VALUES (?,?,?) "
                  "ON CONFLICT(pattern) DO UPDATE SET category = excluded.category", (pattern, category, now()))
    return pattern
