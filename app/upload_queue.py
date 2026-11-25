"""Receipt uploads that arrive while no model is available are kept here and read once the model is back.

add()      upload endpoint: model offline -> file is already saved, remember it as 'waiting'
process()  read waiting receipts oldest first; stops at the first "model offline" and leaves the rest waiting
worker()   background loop in the web app: checks every minute whether the model is back
"""
import asyncio
import logging
import time

from .config import UPLOAD_DIR
from .db import db, now

log = logging.getLogger("ledger.queue")
_lock = asyncio.Lock()
STATE = {"offline_until": 0.0, "offline_reason": "", "running": False}
OFFLINE_CACHE_S = 60   # after the model was found offline, queue new uploads at once instead of waiting for timeouts


def mark_offline(reason: str):
    STATE.update(offline_until=time.monotonic() + OFFLINE_CACHE_S, offline_reason=reason)


def known_offline() -> str | None:
    return STATE["offline_reason"] if time.monotonic() < STATE["offline_until"] else None


def add(path, original_name: str, reason: str) -> int:
    from .store import rel
    with db() as c:
        return c.execute("INSERT INTO upload_queue(kind, file_path, original_name, reason, created_at) VALUES ('receipt', ?, ?, ?, ?)",
                         (rel(path), original_name, reason[:400], now())).lastrowid


def items(include_done_hours: int = 24) -> list[dict]:
    with db() as c:
        return c.execute("SELECT * FROM upload_queue WHERE status != 'done' OR processed_at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime', ?) "
                         "ORDER BY id", (f"-{include_done_hours} hours",)).fetchall()


def waiting_count() -> int:
    with db() as c:
        return c.execute("SELECT COUNT(*) AS n FROM upload_queue WHERE status = 'waiting'").fetchone()["n"]


def _set(qid: int, **f):
    with db() as c:
        c.execute(f"UPDATE upload_queue SET {', '.join(k + ' = ?' for k in f)} WHERE id = ?", [*f.values(), qid])


async def process(limit: int = 50) -> dict:
    """Read waiting receipts. Returns counts; 'offline' is the reason when it stopped because the model is gone."""
    from . import store
    from .documents import extract_document
    from .llm import LLMOffline
    from .reconcile import auto_match
    stats = {"done": 0, "errors": 0, "waiting": 0, "offline": None}
    if _lock.locked():
        return {**stats, "busy": True}
    async with _lock:
        STATE["running"] = True
        try:
            with db() as c:
                rows = c.execute("SELECT * FROM upload_queue WHERE status = 'waiting' ORDER BY id LIMIT ?", (limit,)).fetchall()
            for q in rows:
                path = UPLOAD_DIR / q["file_path"]
                _set(q["id"], status="reading", last_try_at=now(), attempts=q["attempts"] + 1)
                try:
                    if not path.exists():
                        raise FileNotFoundError("the uploaded file is gone")
                    d = await extract_document(path, "receipt")
                    rid, _ = store.save_receipt(path, q["original_name"], d, notes_prefix=f"Uploaded {q['created_at'][:16].replace('T', ' ')}, read later (model was offline).")
                    _set(q["id"], status="done", receipt_id=rid, document_type=d.get("document_type"), processed_at=now(),
                         reason="; ".join(d.get("warnings") or [])[:400])
                    stats["done"] += 1
                except LLMOffline as e:
                    mark_offline(str(e))
                    _set(q["id"], status="waiting", reason=str(e)[:400])
                    stats["offline"] = str(e)
                    break
                except Exception as e:          # bad file, model error on this document...: keep it, user can retry or remove
                    log.exception("queued receipt %s failed", q["id"])
                    _set(q["id"], status="error", reason=f"{type(e).__name__}: {e}"[:400], processed_at=now())
                    stats["errors"] += 1
            if stats["done"]:
                auto_match()
        finally:
            STATE["running"] = False
    stats["waiting"] = waiting_count()
    return stats


async def model_ready() -> bool:
    from .llm import LLM, LLMError, LLMOffline
    try:
        await LLM().resolve_model()
        return True
    except LLMOffline as e:
        mark_offline(str(e))
    except LLMError:
        pass
    return False


async def worker():
    """Every minute: if receipts are waiting and the model answers, read them."""
    with db() as c:   # an item that was being read when the app stopped
        c.execute("UPDATE upload_queue SET status = 'waiting' WHERE status = 'reading'")
    await asyncio.sleep(30)
    while True:
        try:
            if waiting_count() and not _lock.locked() and await model_ready():
                st = await process()
                if st["done"] or st["errors"]:
                    log.info("upload queue: %s", st)
        except Exception:
            log.exception("upload queue worker")
        await asyncio.sleep(60)
