"""Chat assistant: the local model with bookkeeping tools, streamed to the browser as events."""
import asyncio
import json
import re

from .db import get_settings
from .llm import LLM, LLMError
from .skills import system_prompt
from .tools import LEDGER_SCHEMA_DOC, TOOL_DEFS, call_tool

MAX_ROUNDS = 12
# Qwen runs with a 32k-token context. The system prompt + tools take ~7k and an answer may need up to ~12k,
# so the conversation itself is kept under ~34k characters (~10k tokens) by shortening the oldest tool results.
HISTORY_BUDGET = 34000


def _fit(messages: list[dict]):
    def size():
        return sum(len(m.get("content") or "") + len(json.dumps(m.get("tool_calls") or "")) for m in messages[1:])
    for m in messages[1:-2]:  # 1) shorten older tool results, never the newest ones
        if size() <= HISTORY_BUDGET:
            return
        if m["role"] == "tool" and len(m["content"]) > 300:
            m["content"] = m["content"][:300] + '..."(older tool result shortened to save context)"'
    # 2) still too big: drop whole earlier turns, keeping the latest question and everything after it
    last_user = max((i for i, m in enumerate(messages) if m["role"] == "user"), default=1)
    while size() > HISTORY_BUDGET and last_user > 1:
        messages.pop(1)
        last_user -= 1


TASK = f"""You are chatting with the owner of these books inside their ledger app.
- Answer from the ledger: look figures up with the tools, never estimate or recall them. If the tools return nothing
  for a period, say that there is no data for it (see "In the ledger now") instead of answering 0.
- One good tool call beats several: ledger_overview for period totals, run_sql for anything specific.
- If a tool returns an error, read it, fix the call and try once more before telling the user.
- Do not show SQL or tool names unless the user asks how you got a figure.
- Answer in the language the user writes in. Keep it short: the figure first, then at most a small table.

{LEDGER_SCHEMA_DOC}"""


# Skills that are only needed when the conversation is about their subject (each is ~1k tokens of a 32k context).
ON_TOPIC = {
    "vat-and-tax": re.compile(r"moms|\bvat\b|skat|\btax|fradrag|deduct|told\b|reverse charge|revisor|accountant", re.I),
    "reconciliation": re.compile(r"reconcil|afstem|match|\blink|receipt|kvittering|bilag|document|missing|mangl|invoice|"
                                 r"faktura|unpaid|ubetalt|overdue|forfald|\bcards?\b|\bkort\b|cash|kontant|month.?end|"
                                 r"måneds|statement|kontoudtog", re.I),
}


def build_system(conversation: str | None = None) -> str:
    """conversation = what the user has written so far; None = include every skill (the Settings preview)."""
    extra = [name for name, pat in ON_TOPIC.items()
             if conversation is None or pat.search(conversation)
             or (name == "vat-and-tax" and get_settings().get("books_type") == "business")]
    return system_prompt("financial-overviews", "transaction-categorization", *extra,
                         task=TASK, mode="chat", snapshot=True)


def _short(result) -> str:
    if isinstance(result, dict):
        if "error" in result:
            return "error: " + str(result["error"])[:200]
        if result.get("duplicate"):
            return "duplicate: " + str(result["duplicate"])[:120]
        bits = []
        for k in ("row_count", "match_count", "updated", "inserted", "count", "newly_linked", "categorized",
                  "still_uncategorized", "uncategorized_transactions", "payees", "receipt_id", "invoice_id", "filename"):
            if k in result:
                bits.append(f"{k.replace('_', ' ')}: {result[k]}")
        return ", ".join(bits) or "ok"
    return "ok"


async def run_chat(history: list[dict]):
    llm = LLM()
    asked = " ".join(m.get("content") or "" for m in history[-20:] if m.get("role") == "user")
    messages = [{"role": "system", "content": build_system(asked)}]
    for m in history[-20:]:
        if m.get("role") in ("user", "assistant") and m.get("content"):
            messages.append({"role": m["role"], "content": m["content"]})
    try:
        for _ in range(MAX_ROUNDS):
            _fit(messages)
            msg = await llm.chat(messages, tools=TOOL_DEFS, thinking=llm.think_chat, temperature=0.3, max_tokens=12000)
            calls = msg.get("tool_calls") or []
            if not calls:
                yield {"type": "answer", "content": (msg.get("content") or "").strip() or "(no answer)"}
                return
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for call in calls:
                name = call["function"]["name"]
                try:
                    args = json.loads(call["function"].get("arguments") or "{}")
                except ValueError:
                    args = None
                yield {"type": "tool", "name": name, "args": args or {}}
                if args is None:  # tell the model instead of silently running the tool without arguments
                    result = {"error": "The tool arguments were not valid JSON - send the call again."}
                else:
                    result = await asyncio.to_thread(call_tool, name, args)
                yield {"type": "tool_result", "name": name, "summary": _short(result)}
                if isinstance(result, dict) and result.get("download_url"):
                    yield {"type": "file", "url": result["url"], "filename": result["filename"]}
                text = json.dumps(result, default=str, ensure_ascii=False)
                if len(text) > 12000:
                    text = text[:12000] + '..."(truncated - narrow the query or aggregate)"'
                messages.append({"role": "tool", "tool_call_id": call.get("id", name), "content": text})
        yield {"type": "answer", "content": "I stopped after too many tool calls. Try a more specific question."}
    except LLMError as e:
        yield {"type": "error", "content": str(e)}
