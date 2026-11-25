"""Fiat currency conversion using the ECB's daily euro reference rates (cached in data/fx).

Cross rates are derived via EUR, e.g. USD->DKK = DKK_per_EUR / USD_per_EUR. For a weekend or holiday the
last published rate before that date is used, which is the usual bookkeeping practice.
"""
import bisect
import csv
import io
import threading
import time
import zipfile
from datetime import date

import httpx

from .config import DATA_DIR

URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip"
FX_DIR = DATA_DIR / "fx"
CACHE = FX_DIR / "eurofxref-hist.csv"
FX_DIR.mkdir(parents=True, exist_ok=True)
_lock = threading.Lock()
_state = {"dates": [], "rates": {}, "loaded_mtime": 0.0, "last_try": 0.0}


class FXError(Exception):
    pass


def _download():
    r = httpx.get(URL, timeout=30, headers={"User-Agent": "local-ledger/1.0"}, follow_redirects=True)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_bytes(z.read(z.namelist()[0]))
    tmp.replace(CACHE)


def _parse():
    rows = list(csv.reader(io.StringIO(CACHE.read_text())))
    head = [h.strip() for h in rows[0]]
    rates = {}
    for r in rows[1:]:
        if not r or not r[0]:
            continue
        day = {"EUR": 1.0}
        for cur, v in zip(head[1:], r[1:]):
            v = v.strip()
            if cur and v and v != "N/A":
                day[cur] = float(v)
        rates[r[0]] = day
    _state["rates"] = rates
    _state["dates"] = sorted(rates)
    _state["loaded_mtime"] = CACHE.stat().st_mtime


def ensure(need_date: str | None = None, force: bool = False):
    """Make sure rates are loaded; download when missing, forced, or older than the date we need."""
    with _lock:
        have = CACHE.exists()
        last = _state["dates"][-1] if _state["dates"] else None
        too_old = need_date and last and need_date > last and time.time() - CACHE.stat().st_mtime > 6 * 3600
        if force or not have or too_old:
            if force or time.time() - _state["last_try"] > 600:  # don't hammer the ECB when offline
                _state["last_try"] = time.time()
                try:
                    _download()
                except Exception as e:  # offline: keep using the cache if we have one
                    if not CACHE.exists():
                        raise FXError(f"Could not download exchange rates from the ECB ({e}). Check the internet connection.")
        if CACHE.exists() and _state["loaded_mtime"] != CACHE.stat().st_mtime:
            _parse()
        if not _state["dates"]:
            raise FXError("No exchange rates available.")


def status() -> dict:
    try:
        ensure()
    except FXError as e:
        return {"ok": False, "error": str(e)}
    last = _state["dates"][-1]
    return {"ok": True, "source": "ECB euro reference rates", "first_date": _state["dates"][0], "last_date": last,
            "currencies": sorted(_state["rates"][last]),
            "downloaded": time.strftime("%Y-%m-%d %H:%M", time.localtime(CACHE.stat().st_mtime))}


def rate(frm: str, to: str, on: str | None = None) -> dict:
    """Units of `to` per 1 unit of `frm` on date `on` (YYYY-MM-DD)."""
    frm, to = (frm or "").upper().strip(), (to or "").upper().strip()
    on = (on or date.today().isoformat())[:10]
    if frm == to:
        return {"rate": 1.0, "rate_date": on, "source": "same"}
    ensure(on)
    dates = _state["dates"]
    i = bisect.bisect_right(dates, on) - 1
    if i < 0:
        raise FXError(f"No ECB rate on or before {on} (data starts {dates[0]}).")
    # walk back if one of the currencies was not quoted that day
    for j in range(i, max(i - 10, -1), -1):
        day = _state["rates"][dates[j]]
        if frm in day and to in day:
            return {"rate": round(day[to] / day[frm], 6), "rate_date": dates[j], "source": "ecb"}
    known = sorted(_state["rates"][dates[i]])
    missing = frm if frm not in _state["rates"][dates[i]] else to
    raise FXError(f"The ECB has no rate for {missing}. Known currencies: {', '.join(known)}. Enter the rate manually.")


def convert(amount: float, frm: str, to: str, on: str | None = None) -> dict:
    r = rate(frm, to, on)
    return {**r, "amount": amount, "from": frm.upper(), "to": to.upper(), "converted": round(amount * r["rate"], 2)}


def safe_rate(frm: str, to: str, on: str | None) -> tuple[float | None, str]:
    """(rate, source) - never raises; source is 'missing' when no rate could be found."""
    try:
        r = rate(frm, to, on)
        return r["rate"], r["source"]
    except FXError:
        return None, "missing"
