"""Client for the model: any server that speaks the OpenAI chat-completions API.

That covers local servers (Unsloth Studio, LM Studio, Ollama, llama.cpp) and hosted APIs. They agree on the basics and
differ in the details - one rejects `chat_template_kwargs`, another wants `max_completion_tokens`, a third accepts a
JSON schema but ignores it. Instead of a setting per detail, the client LEARNS them: when a server answers 400 and
names a parameter, that parameter is dropped or replaced and the request is sent again; when a schema was sent but
the answer does not follow it, the schema is also written into the prompt. What was learned is remembered per
server + model (setting `llm_quirks`), so each lesson costs one failed request, once.
"""
import base64
import copy
import io
import json
import re

import httpx
from PIL import Image, ImageOps

from .db import get_settings, set_settings

# Starting points offered in the setup screen. `url` is where the server listens by default on the same machine.
PRESETS = [
    {"id": "studio", "label": "Unsloth Studio", "kind": "local", "url": "http://127.0.0.1:8888/v1", "key": "optional",
     "note": "Load a vision model in Studio. The ledger uses whichever model is loaded."},
    {"id": "lmstudio", "label": "LM Studio", "kind": "local", "url": "http://127.0.0.1:1234/v1", "key": "no",
     "note": "Start the local server in LM Studio (Developer tab) and pick the model below."},
    {"id": "ollama", "label": "Ollama", "kind": "local", "url": "http://127.0.0.1:11434/v1", "key": "no",
     "note": "Pull a model first (ollama pull ...) and pick it below. A vision model is needed for photos."},
    {"id": "llamacpp", "label": "llama.cpp server", "kind": "local", "url": "http://127.0.0.1:8080/v1", "key": "optional",
     "note": "llama-server with a vision model (--mmproj) for photos."},
    {"id": "openai", "label": "OpenAI", "kind": "cloud", "url": "https://api.openai.com/v1", "key": "yes",
     "note": "Your documents are sent to OpenAI. Create an API key in your OpenAI account."},
    {"id": "openrouter", "label": "OpenRouter", "kind": "cloud", "url": "https://openrouter.ai/api/v1", "key": "yes",
     "note": "Your documents are sent to OpenRouter and the model provider you pick there."},
    {"id": "custom", "label": "Other (OpenAI-compatible)", "kind": "custom", "url": "", "key": "optional",
     "note": "Any server with /v1/chat/completions. Enter its address including /v1."},
]


class LLMError(Exception):
    pass


class LLMOffline(LLMError):
    """No model to talk to right now (server unreachable, no model loaded, API key missing, gateway errors, rate limit).
    Work that needs the model can wait and be retried later; other LLMErrors are about the request itself."""


class LLMWrongModel(LLMOffline):
    """A model answers, but it cannot do this job (a text-only model was asked to look at an image).
    The work waits until a suitable model is loaded - same handling as offline."""


class LLMBadRequest(LLMError):
    """The server refused the request as malformed (HTTP 400/404/422). `raw` is its own words - the client reads them
    to find out which parameter this server does not take."""

    def __init__(self, message: str, status: int, raw: str):
        super().__init__(message)
        self.status, self.raw = status, raw


NO_VISION = ("does not support vision", "does not support image", "image input is not supported", "mmproj",
             "image_url is only supported", "do not support image", "doesn't support image", "no endpoints found that support image",
             "multimodal", "vision is not supported")
NO_TOOLS = ("does not support tools", "tool use is not supported", "does not support function", "tools is not supported",
            "no endpoints found that support tool")
DEFAULT_QUIRKS = {"thinking": "template", "json": "schema", "max_key": "max_tokens", "temperature": True}


def _friendly(status: int, text: str) -> str:
    low = text.lower()
    if any(k in low for k in NO_VISION):
        return ("The model that is loaded cannot read images (it is a text-only model). Load or choose a vision model - "
                "photos and scans wait in the queue and are read once one is available.")
    if any(k in low for k in NO_TOOLS):
        return ("This model cannot call tools, which the Assistant needs. Choose a model with tool (function) calling "
                "in Settings > Model. Reading documents works without it.")
    if "no model loaded" in low:
        return ("No model is loaded on the model server. Load a model there (a vision model reads receipt photos), "
                "then try again.")
    if status in (401, 403):
        return ("The model server refused the API key. Enter a valid key under Settings > Model > API key and press Save.")
    if status == 402:
        return "The model provider says the account has no credit left. Top it up, or choose another model."
    if status == 429:
        return "The model provider is rate limiting requests right now. Waiting work is retried automatically."
    if status == 404:
        return f"The model server does not know this model or address (HTTP 404): {text[:300]}"
    return f"Model server returned HTTP {status}: {text[:400]}"


def strictify(schema: dict) -> dict:
    """A copy of a JSON schema in the strict form every server accepts: objects list all their properties as required
    and allow no others."""
    s = copy.deepcopy(schema)

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["required"] = list(node["properties"])
                node["additionalProperties"] = False
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(s)
    return s


class LLM:
    def __init__(self, overrides: dict | None = None):
        s = {**get_settings(), **(overrides or {})}
        self.urls = [u.rstrip("/") for u in (s["llm_base_url"], s.get("llm_fallback_url") or "") if u.strip()]
        self.model = (s.get("llm_model") or "").strip()
        self.key = (s.get("llm_api_key") or "").strip()
        self.vision = s.get("llm_vision") or "auto"
        # reasoning effort for reading documents: off | low | medium | xhigh (the Qwen template's default is xhigh).
        # 'off' is fast; documents that fail the checks are re-read with 'xhigh' (see documents.py / importers.py).
        self.effort_docs = s.get("llm_doc_reasoning") or "off"
        self.think_docs = self.effort_docs != "off"
        self.think_chat = s.get("llm_thinking_chat") == "1"
        self._persist = True   # lessons are keyed by server + model, so a test of an unsaved address is worth keeping too
        try:
            self._all_quirks = json.loads(s.get("llm_quirks") or "{}")
        except ValueError:
            self._all_quirks = {}

    # ---------------------------------------------------------------- what this server accepts

    def _key(self, model: str) -> str:
        return f"{self.urls[0] if self.urls else ''}|{model}"

    def quirks(self, model: str) -> dict:
        return {**DEFAULT_QUIRKS, **self._all_quirks.get(self._key(model), {})}

    def _remember(self, model: str, q: dict):
        self._all_quirks[self._key(model)] = {k: v for k, v in q.items() if DEFAULT_QUIRKS.get(k) != v}
        if self._persist:
            set_settings({"llm_quirks": json.dumps(self._all_quirks)})

    @staticmethod
    def _adapt(q: dict, raw: str, body: dict) -> str | None:
        """Change `q` according to the server's complaint. Returns what was changed, or None if nothing applies."""
        t = raw.lower()
        unknown = any(k in t for k in ("unrecognized request argument", "extra inputs are not permitted", "unknown parameter",
                                       "unknown field", "unexpected keyword", "additional properties are not allowed",
                                       "unsupported parameter", "is not supported with this model", "not permitted"))
        if "chat_template_kwargs" in body and ("chat_template_kwargs" in t or "enable_thinking" in t):
            q["thinking"] = "none"
            return "thinking parameters dropped"
        if "response_format" in body and any(k in t for k in ("response_format", "json_schema", "json schema", "structured output",
                                                              "grammar", "json mode")):
            q["json"] = "object" if q["json"] in ("schema", "schema+prompt") else "prompt"
            return f"structured output: {q['json']}"
        if "max_tokens" in body and "max_completion_tokens" in t:
            q["max_key"] = "max_completion_tokens"
            return "max_completion_tokens instead of max_tokens"
        if "temperature" in body and "temperature" in t:
            q["temperature"] = False
            return "temperature dropped"
        if unknown:     # the server did not say which one: give up the least standard parameter still in use
            if "chat_template_kwargs" in body:
                q["thinking"] = "none"
                return "thinking parameters dropped"
            if body.get("response_format", {}).get("type") == "json_schema":
                q["json"] = "object"
                return "structured output: object"
        return None

    def _body(self, model, q, messages, tools, schema, thinking, effort, temperature, max_tokens) -> dict:
        body = {"model": model, "messages": messages, q["max_key"]: max_tokens}
        if q["temperature"]:
            body["temperature"] = temperature
        if q["thinking"] == "template":   # Qwen-style chat templates (llama.cpp, Unsloth Studio) switch reasoning this way
            kwargs = {"enable_thinking": bool(thinking)}
            if thinking and effort in ("low", "medium", "xhigh"):
                kwargs["reasoning_effort"] = effort
            body["chat_template_kwargs"] = kwargs
        if tools:
            body["tools"] = tools
        if schema:
            strict = strictify(schema)
            if q["json"] in ("schema", "schema+prompt"):
                body["response_format"] = {"type": "json_schema",
                                           "json_schema": {"name": "result", "schema": strict, "strict": True}}
            elif q["json"] == "object":
                body["response_format"] = {"type": "json_object"}
            if q["json"] != "schema":     # the server does not enforce the schema (or may not): spell it out
                note = ("\n\n## Output format\nAnswer with one JSON object and nothing else - no explanation, no code fence. "
                        "It must match this JSON Schema exactly (every key present, null where a value is unknown):\n"
                        + json.dumps(strict, ensure_ascii=False, separators=(",", ":")))
                msgs = [dict(m) for m in messages]
                if msgs and msgs[0]["role"] == "system" and isinstance(msgs[0]["content"], str):
                    msgs[0]["content"] += note
                else:
                    msgs.insert(0, {"role": "system", "content": note.strip()})
                body["messages"] = msgs
        return body

    # ---------------------------------------------------------------- transport

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.key:
            h["Authorization"] = f"Bearer {self.key}"
        return h

    async def _request(self, method: str, path: str, payload=None, timeout=900.0, connect=5.0):
        last = None
        for url in self.urls:
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=connect)) as client:
                    r = await client.request(method, url + path, json=payload, headers=self._headers())
                if r.status_code >= 400:
                    low = r.text.lower()
                    if any(k in low for k in NO_VISION):
                        raise LLMWrongModel(_friendly(r.status_code, r.text))
                    if r.status_code in (401, 403, 429, 502, 503, 504) or "no model loaded" in low:
                        raise LLMOffline(_friendly(r.status_code, r.text))
                    if r.status_code in (400, 404, 422):
                        raise LLMBadRequest(_friendly(r.status_code, r.text), r.status_code, r.text)
                    raise LLMError(_friendly(r.status_code, r.text))
                return r.json()
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                last = e
                continue  # try the fallback address
        if not self.urls:
            raise LLMOffline("No model server is configured. Enter its address under Settings > Model.")
        raise LLMOffline(f"Cannot reach the model server at {' or '.join(self.urls)} ({last}). Is it running?")

    # ---------------------------------------------------------------- models

    async def status(self, connect=5.0) -> dict:
        data = await self._request("GET", "/models", timeout=15, connect=connect)
        models = data.get("data", []) if isinstance(data, dict) else []
        # some servers (Unsloth Studio, llama.cpp router) say which model is loaded; most just list what they offer
        reports = any("loaded" in m or "status" in m for m in models)
        loaded = [m["id"] for m in models if m.get("loaded") or (m.get("status") or {}).get("value") == "loaded"]
        ids = [m["id"] for m in models if m.get("id")]
        using = self.model or (loaded[0] if loaded else (ids[0] if not reports and len(ids) == 1 else None))
        return {"loaded": loaded, "configured": self.model, "using": using, "available": sorted(ids)[:400],
                "reports_loaded": reports, "needs_choice": not using and bool(ids) and not reports}

    async def resolve_model(self) -> str:
        if self.model:
            return self.model
        st = await self.status()
        if st["using"]:
            return st["using"]
        if st["needs_choice"]:
            raise LLMOffline(f"The model server offers {len(st['available'])} models - choose one under Settings > Model.")
        raise LLMOffline(_friendly(400, "No model loaded"))

    # ---------------------------------------------------------------- requests

    async def chat(self, messages, *, tools=None, schema=None, thinking=False, effort=None, temperature=0.2,
                   max_tokens=12000) -> dict:
        model = await self.resolve_model()
        q = self.quirks(model)
        for _ in range(6):
            body = self._body(model, q, messages, tools, schema, thinking, effort, temperature, max_tokens)
            try:
                data = await self._request("POST", "/chat/completions", body)
                break
            except LLMBadRequest as e:
                if any(k in e.raw.lower() for k in NO_TOOLS) or not self._adapt(q, e.raw, body):
                    raise
                self._remember(model, q)
        else:
            raise LLMError("The model server keeps rejecting the request - see Settings > Model > Test.")
        choice = data["choices"][0]
        msg = choice["message"]
        msg["_finish_reason"] = choice.get("finish_reason")
        return msg

    async def extract(self, system: str, content, schema: dict, *, thinking=None, effort=None, max_tokens=12000) -> dict:
        """Ask for JSON matching `schema`. `content` is a string or a list of OpenAI content parts.
        thinking=None uses the document reasoning setting; effort='xhigh' forces a careful re-read."""
        if effort:
            thinking = effort != "off"
        elif thinking is None:
            effort, thinking = self.effort_docs, self.think_docs
        messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]
        msg = await self.chat(messages, schema=schema, thinking=thinking, effort=effort, max_tokens=max_tokens)
        out = _structured(msg, schema)
        if out is not None:
            return out
        if msg.get("_finish_reason") == "length":
            raise LLMError("The model ran out of tokens before finishing. Try a smaller file or fewer pages.")
        # A schema was sent and the answer does not follow it: this server accepts the schema without enforcing it.
        # Put the schema into the prompt as well (and remember that), then ask once more - without reasoning, which
        # is also the usual cure for malformed output.
        model = await self.resolve_model()
        q = self.quirks(model)
        if q["json"] == "schema":
            q["json"] = "schema+prompt"
            self._remember(model, q)
        msg = await self.chat(messages, schema=schema, thinking=False, max_tokens=max_tokens)
        out = _structured(msg, schema)
        if out is None:
            raise LLMError("The model did not answer in the requested JSON format. A model with structured output "
                           "(JSON schema) support reads documents far more reliably.")
        return out


def _structured(msg: dict, schema: dict):
    """The parsed answer if it is JSON with the schema's top-level keys, else None."""
    try:
        out = parse_json(msg.get("content") or "")
    except ValueError:
        return None
    need = schema.get("required") or list(schema.get("properties", {}))
    if not isinstance(out, dict) or any(k not in out for k in need):
        return None
    return out


def parse_json(text: str):
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()   # reasoning some servers leave in the text
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except ValueError:
        pass
    m = re.search(r"[\[{].*[\]}]", text, re.S)
    if m:
        return json.loads(m.group(0))
    raise ValueError("Model did not return JSON")


def image_part(path_or_bytes, max_side=1800) -> dict:
    """Downscale an image and wrap it as an OpenAI image_url content part."""
    img = Image.open(io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, bytes) else path_or_bytes)
    img = ImageOps.exif_transpose(img).convert("RGB")  # phone photos: honour the camera's rotation flag
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return {"type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()}}


# ---------------------------------------------------------------- "Test" in the setup screen and in Settings

def _test_image(text: str = "4711") -> bytes:
    from PIL import ImageDraw
    small = Image.new("RGB", (60, 22), "white")
    ImageDraw.Draw(small).text((6, 5), text, fill="black")
    big = small.resize((480, 176), Image.LANCZOS)
    buf = io.BytesIO()
    big.save(buf, "PNG")
    return buf.getvalue()


async def probe(base_url: str, api_key: str = "", model: str = "") -> dict:
    """Try a server the way the ledger will use it: list models, structured output, an image, a tool call.
    Nothing is saved; every step reports ok / not ok with the server's own reason."""
    llm = LLM({"llm_base_url": base_url, "llm_fallback_url": "", "llm_api_key": api_key, "llm_model": model})
    out = {"reachable": False, "models": [], "using": None, "needs_choice": False, "json": None, "vision": None, "tools": None}
    try:
        st = await llm.status(connect=4.0)
    except LLMError as e:
        return {**out, "error": str(e)}
    out.update(reachable=True, models=st["available"], using=st["using"], needs_choice=st["needs_choice"])
    if not st["using"]:
        out["error"] = ("Choose one of the models." if st["needs_choice"] else "The server is reachable but no model is loaded.")
        return out
    llm.model = st["using"]
    schema = {"type": "object", "properties": {"answer": {"type": "integer"}}, "required": ["answer"]}
    try:
        r = await llm.extract("You are a calculator. Answer in JSON.", "What is 17 + 25?", schema, thinking=False, max_tokens=200)
        mode = llm.quirks(llm.model)["json"]
        out["json"] = {"ok": r.get("answer") == 42, "mode": mode,
                       "detail": {"schema": "the server enforces the JSON schema", "schema+prompt": "schema accepted but not enforced - it is also written into the prompt",
                                  "object": "JSON mode without schema - the schema is written into the prompt",
                                  "prompt": "no JSON mode - the schema is written into the prompt"}[mode]
                       + ("" if r.get("answer") == 42 else f" (answered {r.get('answer')!r} to 17 + 25)")}
    except LLMError as e:
        out["json"] = {"ok": False, "detail": str(e)}
        return out
    try:
        r = await llm.extract("Read the number in the image. Answer in JSON.", [image_part(_test_image()),
                              {"type": "text", "text": "Which number is shown in the image?"}], schema, thinking=False, max_tokens=200)
        ok = r.get("answer") == 4711
        out["vision"] = {"ok": ok, "detail": "reads images" if ok else f"did not read the test image (answered {r.get('answer')!r})"}
    except LLMError as e:
        out["vision"] = {"ok": False, "detail": str(e)}
    tool = [{"type": "function", "function": {"name": "add", "description": "Add two numbers.", "parameters": {
        "type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}, "required": ["a", "b"]}}}]
    try:
        m = await llm.chat([{"role": "user", "content": "Use the add tool to add 2 and 3."}], tools=tool, thinking=False, max_tokens=300)
        ok = bool(m.get("tool_calls"))
        out["tools"] = {"ok": ok, "detail": "calls tools" if ok else "answered without calling the tool - the Assistant needs tool calling"}
    except LLMError as e:
        out["tools"] = {"ok": False, "detail": str(e)}
    out["quirks"] = {k: v for k, v in llm.quirks(llm.model).items() if DEFAULT_QUIRKS.get(k) != v}
    return out
