"""Stand-in for Unsloth Studio's OpenAI-compatible API, for testing the ledger without the GPU PC.

It cannot judge prompt QUALITY - it only proves that requests are well-formed, that answers flow through the app,
and it records every request so the exact prompts can be inspected (requests.jsonl).

POST /mode changes how it behaves, to imitate other kinds of servers:
  {"vision": false}            a text-only model (images are refused)
  {"strict": true}             an API that rejects chat_template_kwargs, max_tokens and a custom temperature
  {"schema": "reject"}         no json_schema support (400); {"schema": "reject_all"} also refuses json_object
  {"schema": "ignore"}         accepts response_format but answers free text unless the schema is in the prompt
  {"tools": false}             a model without tool calling
  {"models": "many"}           /v1/models lists three models and does not say which one is loaded
"""
import json, os, re, sys
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

LOG = os.environ.get("FAKE_LOG", "requests.jsonl")
MODE = {"vision": True, "strict": False, "schema": "ok", "tools": True, "models": "one"}
app = FastAPI()


def instance(schema, hint=""):
    """A minimal value that satisfies a JSON schema."""
    t = schema.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "null")
    if "enum" in schema:
        return schema["enum"][0]
    if t == "object":
        return {k: instance(v, k) for k, v in schema.get("properties", {}).items()}
    if t == "array":
        return []
    if t == "string":
        return ""
    if t in ("number", "integer"):
        return 0
    if t == "boolean":
        return False
    return None


KEYWORD_CATS = [("danske spil", "Leisure, games & hobbies"), ("mobilepay", "Friends & family"), ("viabill", "Shopping & clothing"),
                ("lunar", "Bank fees"), ("silverback", "Health & personal"), ("amazon", "Shopping & clothing")]


def answer(body):
    msgs = body["messages"]
    sys_text = msgs[0]["content"] if msgs and msgs[0]["role"] == "system" else ""
    user = msgs[-1]["content"]
    user_text = user if isinstance(user, str) else " ".join(p.get("text", "") for p in user if p.get("type") == "text")
    has_image = not isinstance(user, str) and any(p.get("type") == "image_url" for p in user)
    if has_image and not MODE["vision"]:
        return 400, {"error": {"message": "Invalid content type. image_url is only supported by certain models.", "type": "invalid_request_error"}}
    if MODE["strict"]:
        if "chat_template_kwargs" in body:
            return 400, {"error": {"message": "Unrecognized request argument supplied: chat_template_kwargs", "type": "invalid_request_error"}}
        if "max_tokens" in body:
            return 400, {"error": {"message": "Unsupported parameter: 'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead."}}
        if body.get("temperature", 1) != 1:
            return 400, {"error": {"message": "Unsupported value: 'temperature' does not support 0.2 with this model. Only the default (1) value is supported."}}
    if body.get("tools") and not MODE["tools"]:
        return 400, {"error": {"message": "registry.example/test-model does not support tools"}}
    fmt = body.get("response_format") or {}
    if fmt.get("type") == "json_schema" and MODE["schema"] in ("reject", "reject_all"):
        return 400, {"error": {"message": "This model does not support response_format of type json_schema."}}
    if fmt.get("type") == "json_object" and MODE["schema"] == "reject_all":
        return 400, {"error": {"message": "'response_format' is not supported by this server."}}
    rf = fmt.get("json_schema", {}).get("schema")
    in_prompt = re.search(r"JSON Schema exactly[^\n]*\n(\{.*\})\s*$", sys_text, re.S)
    if in_prompt and (not rf or MODE["schema"] == "ignore"):      # the schema was written into the prompt: follow that
        rf = json.loads(in_prompt.group(1))
    elif rf and MODE["schema"] == "ignore":
        return 200, {"choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "Sure! The answer is forty-two."}}]}
    if rf:
        out = instance(rf)
        props = rf.get("properties", {})
        if "results" in props:                      # categorization: one answer per numbered payee
            names = props["results"]["items"]["properties"]["category"]["enum"]
            res = []
            for line in user_text.splitlines()[1:]:
                parts = [x.strip() for x in line.split("|")]
                if not parts or not parts[0].isdigit():
                    continue
                text = " ".join(parts[4:6]).lower()
                cat = next((c for k, c in KEYWORD_CATS if k in text and c in names), "Uncategorized")
                m = re.search(r"before: (.+?) x\d+", line)
                if m and m.group(1) in names:
                    cat = m.group(1)
                res.append({"i": int(parts[0]), "category": cat})
            out = {"results": res}
        elif list(props) == ["answer"]:                  # the connection test: 17 + 25, or the number in the test image
            out = {"answer": 4711 if has_image else 42}
        elif "is_purchase_document" in props:
            is_doc = bool(re.search(r"receipt|kvittering|invoice|faktura|order", user_text, re.I)) and "credit-agreement" not in user_text
            out = {"is_purchase_document": is_doc, "document_type": "receipt" if is_doc else "other", "reason": "fake"}
        elif "document_type" in props and "merchant" in props:   # receipt/invoice: read the first plausible total from the text
            nums = re.findall(r"(?:total|i alt|amount paid|beløb)[^\d\n]{0,25}(\d[\d.,]*\d)", user_text, re.I)
            total = None
            if nums:
                t = nums[-1]
                t = t.replace(".", "").replace(",", ".") if re.search(r",\d{2}$", t) else t.replace(",", "")
                try: total = float(t)
                except ValueError: total = None
            d = re.search(r"(20\d\d)-(\d\d)-(\d\d)", user_text)
            out.update(document_type="receipt", direction="purchase", merchant="Fake Merchant", currency="DKK", total=total,
                       date=d.group(0) if d else "2026-11-20", payment_method="card", already_paid=True,
                       category="Software & subscriptions", confidence="high", notes="")
        return 200, {"choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(out)}}]}
    if body.get("tools"):                           # chat: call one tool, then answer from its result
        tool_msgs = [m for m in msgs if m["role"] == "tool"]
        names = [t["function"]["name"] for t in body["tools"]]
        last_user = next(m["content"] for m in reversed(msgs) if m["role"] == "user")
        if not tool_msgs:
            if "add" in names:
                return 200, {"choices": [{"index": 0, "finish_reason": "tool_calls", "message": {"role": "assistant", "content": "",
                             "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "add", "arguments": '{"a": 2, "b": 3}'}}]}}]}
            if "categor" in last_user.lower() and "list_uncategorized" in names:
                call = ("list_uncategorized", {"limit": 5})
            elif "secret" in last_user.lower():
                call = ("run_sql", {"query": "SELECT value FROM settings"})
            elif "badargs" in last_user.lower():
                call = ("search_transactions", {"nonsense": 1, "text": None})
            else:
                call = ("ledger_overview", {"start_date": "2026-01-01", "end_date": "2026-12-31"})
            return 200, {"choices": [{"index": 0, "finish_reason": "tool_calls", "message": {"role": "assistant", "content": "",
                         "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": call[0], "arguments": json.dumps(call[1])}}]}}]}
        return 200, {"choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant",
                     "content": "Tool said: " + tool_msgs[-1]["content"][:300]}}]}
    return 200, {"choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}]}


@app.get("/v1/models")
def models():
    if MODE["models"] == "many":
        return {"object": "list", "data": [{"id": "small-text:1b", "object": "model"}, {"id": "vision-pro:12b", "object": "model"}, {"id": "embed-mini", "object": "model"}]}
    return {"data": [{"id": "fake/Qwen-test", "loaded": True}]}


@app.post("/v1/chat/completions")
async def chat(req: Request):
    body = await req.json()
    status, out = answer(body)
    slim = json.loads(json.dumps(body))
    for m in slim["messages"]:
        if not isinstance(m.get("content"), str) and m.get("content"):
            for p in m["content"]:
                if p.get("type") == "image_url":
                    p["image_url"] = {"url": f"<image {len(p['image_url']['url'])} chars>"}
    with open(LOG, "a") as f:
        f.write(json.dumps({"request": slim, "status": status, "response": out}) + "\n")
    return JSONResponse(out, status_code=status)


@app.post("/mode")
async def mode(req: Request):
    MODE.update(await req.json())
    return MODE


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8898, log_level="warning")
