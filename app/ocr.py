"""Optional OCR pass with a dedicated OCR model (PaddleOCR-VL, served by llama.cpp via run_ocr.sh).

The OCR model transcribes each image first; Qwen then gets the transcription (and, in 'both' mode, the image too),
so every number is read twice. 'Table Recognition:' is used instead of plain 'OCR:' because it keeps each price on
the same row as its label - plain OCR dropped the right-hand price column of receipts in testing.
"""
import base64
import io
import re

import httpx
from PIL import Image, ImageOps

from .db import get_settings
from .llm import image_part

PROMPT = "Table Recognition:"
NOTE_BOTH = ("Machine OCR transcription of the attached image(s): one row per line, cells separated by ' | '. "
             "It is a second reading of the same document - usually right about digits and about which price belongs "
             "to which line, but it often misspells letters (æ ø å). Read the image yourself and compare every amount "
             "with the transcription. Where they disagree, trust the image when it is clearly legible, set confidence "
             "to 'medium' and name the difference in notes.")
NOTE_TEXT = ("Machine OCR transcription of the document (no image attached): one row per line, cells separated by "
             "' | '. It may misspell letters (æ ø å) and occasionally misread a digit. Correct obvious spelling errors "
             "in names, and check the numbers against each other (line items add up to the total; on a Danish receipt "
             "the moms is 20% of the total incl. moms). If they do not add up, set confidence to 'medium' or 'low' "
             "and say so in notes.")


class OCRError(Exception):
    pass


def settings() -> tuple[str, str]:
    s = get_settings()
    return (s.get("ocr_url") or "").rstrip("/"), (s.get("ocr_mode") or "off")


def _png_b64(src) -> str:
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(src) if isinstance(src, bytes) else src)).convert("RGB")
    if max(img.size) > 1800:
        img.thumbnail((1800, 1800))
    elif max(img.size) < 1000:  # small photos: upscaling improves letter recognition
        img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def clean(txt: str) -> str:
    """Turn the model's table tokens (<fcel>, <nl>, ...) into readable ' | ' rows."""
    txt = txt.replace("<nl>", "\n")
    txt = re.sub(r"<(?:fcel|lcel|ucel|xcel|ecel)>", " | ", txt)
    rows = []
    for row in txt.splitlines():
        cells = [c.strip() for c in row.split("|") if c.strip()]
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows)


async def ocr_image(src, url: str | None = None) -> str:
    url = url or settings()[0]
    if not url:
        raise OCRError("No OCR server configured")
    body = {"model": "paddleocr-vl", "temperature": 0, "max_tokens": 4096, "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + _png_b64(src)}},
        {"type": "text", "text": PROMPT}]}]}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=3)) as c:
            r = await c.post(url + "/chat/completions", json=body)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        raise OCRError(f"OCR server not reachable at {url} - start it with ./run_ocr.sh")
    if r.status_code >= 400:
        raise OCRError(f"OCR server error {r.status_code}: {r.text[:200]}")
    return clean(r.json()["choices"][0]["message"]["content"] or "")


async def status() -> dict:
    url, mode = settings()
    if mode == "off" or not url:
        return {"ok": False, "mode": "off"}
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(url + "/models")
        return {"ok": r.status_code == 200, "mode": mode, "model": r.json()["data"][0]["id"]}
    except Exception:
        return {"ok": False, "mode": mode, "error": f"OCR server not reachable at {url}"}


async def image_content(images: list) -> dict:
    """Build the model input for a list of images (paths or bytes) according to the OCR mode.
    Returns {content: [...parts], ocr_text: str | None, warnings: [...]}."""
    url, mode = settings()
    no_vision = (get_settings().get("llm_vision") or "auto") == "no"
    if no_vision:
        if mode == "off" or not url:
            from .llm import LLMError
            raise LLMError("This is a photo or a scan, and the model is set to 'cannot read images' with no OCR server "
                           "configured. Choose a vision model, or set up the OCR server (Settings > Model).")
        mode = "text"   # the main model only gets the transcription
    warnings, texts = [], None
    if mode != "off" and url:
        try:
            texts = [await ocr_image(img, url) for img in images]
        except OCRError as e:
            if no_vision:
                from .llm import LLMOffline
                raise LLMOffline(f"{e} - and the main model cannot read images, so this document waits.")
            warnings.append(f"{e}. Read with the main model only.")
    parts = []
    if not texts or mode != "text":
        parts += [image_part(img) for img in images]
    ocr_text = None
    if texts:
        ocr_text = "\n\n".join(f"--- image {i + 1} ---\n{t}" for i, t in enumerate(texts)) if len(texts) > 1 else texts[0]
        parts.append({"type": "text", "text": (NOTE_TEXT if mode == "text" else NOTE_BOTH) + "\n\n" + ocr_text})
    return {"content": parts, "ocr_text": ocr_text, "warnings": warnings}


def _numbers(text: str) -> set[float]:
    """Every amount that appears in a text, in both decimal conventions ('1.234,56' and '1,234.56')."""
    out = set()
    for m in re.findall(r"-?\d[\d .,']*\d|\d", text or ""):
        # the whole match ("1 234,56") and its space-separated parts ("2 45,00" is a quantity and a price)
        for part in {m, *m.split()}:
            t = part.replace(" ", "").replace("'", "").strip(".,")
            for dec in (",", "."):
                other = "." if dec == "," else ","
                s = t.replace(other, "")
                if s.count(dec) <= 1:
                    try:
                        out.add(round(abs(float(s.replace(dec, "."))), 2))
                    except ValueError:
                        pass
    return out


def cross_check(data: dict, text: str | None, source: str = "the OCR transcription", against: str = "the image") -> list[str]:
    """Flag key amounts the model reported that do not appear in a second reading of the document:
    the OCR transcription of a photo, or the document's own text (digital PDF, email)."""
    if not text:
        return []
    seen = _numbers(text)
    out = []
    for key, label in (("total", "Total"), ("vat", "VAT")):
        v = data.get(key)
        if isinstance(v, (int, float)) and v and round(abs(v), 2) not in seen:
            out.append(f"{label} {v:.2f} does not appear in {source} - check it against {against}.")
    return out
