"""Desktop automation: wake the GPU desktop over the LAN, load the model in Unsloth Studio, do the work that
needs the model, then power the desktop off again.

One run (run_cycle):
  1. scan the email inboxes here on the server (no model needed) and count what is waiting for the model:
     unread email documents, queued receipt uploads, new Uncategorized transactions
  2. nothing waiting and "only when there is work" -> done, the PC is not woken
  3. Studio not answering -> Wake-on-LAN (repeated every minute) and wait for Studio
  4. no model loaded in Studio -> load the configured one (Studio applies its saved launch settings for it)
  5. read queued uploads and email documents, categorize new transactions
  6. unload the model if this run loaded it; power the PC off over SSH if this run woke it, shutting down is
     enabled and nobody is using it (with a 2-minute warning on the PC that can be cancelled)

Power-off: SSH as root to the desktop's Linux with a key whose authorized_keys entry forces
/usr/local/sbin/ledger-remote, so the key can only ask for `status`, `poweroff` or `cancel`. setup_script()
is the one-time setup to run on the desktop.
"""
import asyncio
import json
import logging
import socket
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .config import DATA_DIR
from .db import db, get_settings, now

log = logging.getLogger("ledger.desktop")
SSH_DIR = DATA_DIR / "desktop_ssh"
KEY = SSH_DIR / "id_ed25519"
STUDIO_PORT_FALLBACK = 8888
WINDOWS_PORTS = (4445, 9835)          # OhmGraphite + GPU exporter: only run when the PC is booted into Windows
STATE = {"running": False, "phase": "", "run_id": None, "cancel": False}


class Stop(Exception):
    """End the run early with this status."""

    def __init__(self, status: str, msg: str):
        super().__init__(msg)
        self.status = status


# ---------------------------------------------------------------- settings & schedule

def cfg() -> dict:
    s = get_settings()

    def num(k, d):
        try:
            return float(s.get(k) or d)
        except ValueError:
            return d
    return {
        "auto": s.get("desktop_auto") == "1",
        "every_days": max(1, int(num("desktop_every_days", 3))),
        "time": (s.get("desktop_time") or "03:40").strip(),
        "model": (s.get("desktop_model") or "").strip(),
        "only_if_work": s.get("desktop_only_if_work", "1") == "1",
        "categorize": s.get("desktop_categorize", "1") == "1",
        "shutdown": s.get("desktop_shutdown", "1") == "1",
        "respect_users": s.get("desktop_respect_users", "1") == "1",
        "boot_wait": max(1.0, num("desktop_boot_wait_min", 10)),
        "max_run": max(5.0, num("desktop_max_run_min", 90)),
        "mac": (s.get("desktop_mac") or "").strip(),
        "hosts": [h.strip() for h in (s.get("desktop_hosts") or "").replace(";", ",").split(",") if h.strip()],
        "studio_urls": [u.rstrip("/").removesuffix("/v1") for u in (s.get("llm_base_url"), s.get("llm_fallback_url")) if (u or "").strip()],
        "key": (s.get("llm_api_key") or "").strip(),
        "ocr_mode": s.get("ocr_mode") or "off",
    }


def last_run(trigger: str | None = None, ok_only: bool = False) -> dict | None:
    sql, args = "SELECT * FROM desktop_runs WHERE status != 'running'", []
    if trigger:
        sql += " AND trigger = ?"
        args.append(trigger)
    if ok_only:
        sql += " AND status IN ('ok', 'nothing to do')"
    with db() as c:
        return c.execute(sql + " ORDER BY id DESC LIMIT 1", args).fetchone()


def next_run(c: dict | None = None) -> datetime | None:
    c = c or cfg()
    if not c["auto"]:
        return None
    try:
        hh, mm = (int(x) for x in c["time"].split(":")[:2])
    except ValueError:
        hh, mm = 3, 40
    last = last_run("schedule")
    if last:
        day = datetime.fromisoformat(last["started_at"]).date() + timedelta(days=c["every_days"])
        return datetime.combine(day, datetime.min.time()).replace(hour=hh, minute=mm)
    cand = datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0)
    return cand if cand > datetime.now() else cand + timedelta(days=1)


def runs(limit: int = 15) -> list[dict]:
    with db() as c:
        rows = c.execute("SELECT * FROM desktop_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    for r in rows:
        r["work"], r["done"] = json.loads(r["work"] or "{}"), json.loads(r["done"] or "{}")
    return rows


# ---------------------------------------------------------------- run log

def _log(msg: str):
    line = f"{datetime.now():%H:%M:%S} {msg}"
    log.info("desktop: %s", msg)
    STATE["phase"] = msg
    if STATE["run_id"]:
        with db() as c:
            c.execute("UPDATE desktop_runs SET log = log || ? WHERE id = ?", (line + "\n", STATE["run_id"]))


def _save(**f):
    with db() as c:
        c.execute(f"UPDATE desktop_runs SET {', '.join(k + ' = ?' for k in f)} WHERE id = ?",
                  [json.dumps(v) if isinstance(v, dict) else v for v in f.values()] + [STATE["run_id"]])


# ---------------------------------------------------------------- the desktop: network checks, wake, SSH

def _port_state(host: str, port: int, timeout=2.0) -> str:
    """'open', 'closed' (the machine answered with a reset, so it is on) or 'none' (no answer)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "open"
    except ConnectionRefusedError:
        return "closed"
    except OSError:
        return "none"


async def _any_port(hosts, ports, want=("open",)) -> bool:
    checks = [asyncio.to_thread(_port_state, h, p) for h in hosts for p in ports]
    return any(r in want for r in await asyncio.gather(*checks))


def _studio_hostports(c) -> list[tuple[str, int]]:
    out = []
    for u in c["studio_urls"]:
        p = urlparse(u)
        if p.hostname:
            out.append((p.hostname, p.port or STUDIO_PORT_FALLBACK))
    return out


async def machine_up(c) -> bool:
    """Something on the PC answers (open or refused) on Studio, SSH or the Windows-only ports."""
    hosts = list(dict.fromkeys(c["hosts"] + [h for h, _ in _studio_hostports(c)]))
    return await _any_port(hosts, (22, STUDIO_PORT_FALLBACK) + WINDOWS_PORTS, want=("open", "closed"))


async def windows_up(c) -> bool:
    return await _any_port(c["hosts"], WINDOWS_PORTS)


def send_wol(mac: str):
    hexmac = mac.replace(":", "").replace("-", "").strip()
    if len(hexmac) != 12:
        raise ValueError(f"Wake-on-LAN MAC address looks wrong: {mac!r}")
    pkt = b"\xff" * 6 + bytes.fromhex(hexmac) * 16
    targets = ["255.255.255.255"]
    for h in _local_ips():
        if h.count(".") == 3 and not h.startswith(("127.", "100.", "172.")):
            targets.append(h.rsplit(".", 1)[0] + ".255")      # the LAN's broadcast address (/24 networks)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for addr in dict.fromkeys(targets):
            for port in (9, 7):
                try:
                    s.sendto(pkt, (addr, port))
                except OSError:
                    pass


def _local_ips() -> list[str]:
    """Source addresses this server uses to reach the desktop (for the setup script's `from=` list)."""
    out = []
    for h in cfg()["hosts"]:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((h, 22))
                out.append(s.getsockname()[0])
        except OSError:
            pass
    return list(dict.fromkeys(out))


def ensure_key() -> str:
    """The SSH key pair used for status/power-off (created on first use). Returns the public key line."""
    SSH_DIR.mkdir(parents=True, exist_ok=True)
    SSH_DIR.chmod(0o700)
    if not KEY.exists():
        import subprocess
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "local-ledger-desktop", "-f", str(KEY)], check=True)
    return (SSH_DIR / "id_ed25519.pub").read_text().strip()


async def ssh(cmd: str, timeout=20) -> tuple[bool, str]:
    """Run one allowed command on the desktop. Tries each desktop address; (ok, output or error)."""
    ensure_key()
    last = "no desktop address configured"
    for host in cfg()["hosts"]:
        proc = await asyncio.create_subprocess_exec(
            "ssh", "-F", "/dev/null", "-i", str(KEY), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=6", "-o", "StrictHostKeyChecking=accept-new",
            "-o", f"UserKnownHostsFile={SSH_DIR / 'known_hosts'}", f"root@{host}", cmd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            last = f"{host}: timed out"
            continue
        if proc.returncode == 0:
            return True, out.decode(errors="replace").strip()
        last = f"{host}: {(err or out).decode(errors='replace').strip()[-300:] or 'exit ' + str(proc.returncode)}"
    return False, last


def parse_status(text: str) -> dict:
    """ledger-remote status output -> {boot, grub, sessions: [...], in_use: bool}."""
    st = {"boot": None, "grub": None, "sessions": []}
    for line in text.splitlines():
        key, _, rest = line.partition(" ")
        if key == "boot":
            st["boot"] = rest.strip()
        elif key == "grub":
            st["grub"] = rest.strip()
        elif key == "session":
            st["sessions"].append(dict(kv.split("=", 1) for kv in rest.split() if "=" in kv))
    # somebody at the PC: a local user session that is in front (Active) and not idle
    st["in_use"] = any(s.get("Class") == "user" and s.get("Remote") == "no" and s.get("Active") == "yes"
                       and s.get("IdleHint") == "no" for s in st["sessions"])
    return st


# ---------------------------------------------------------------- Unsloth Studio

async def studio(method: str, path: str, body=None, timeout=20.0) -> dict:
    c = cfg()
    headers = {"Authorization": f"Bearer {c['key']}"} if c["key"] else {}
    last = None
    for base in c["studio_urls"]:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0)) as cl:
                r = await cl.request(method, base + path, json=body, headers=headers)
            if r.status_code >= 400:
                raise RuntimeError(f"Studio {path}: HTTP {r.status_code} {r.text[:300]}")
            return r.json()
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
            last = e
    raise ConnectionError(f"Studio not reachable ({last})")


async def studio_models() -> list[dict] | None:
    """Studio's model list, or None when it does not answer (an HTTP error, e.g. a wrong API key, is raised)."""
    try:
        return (await studio("GET", "/v1/models", timeout=8))["data"]
    except ConnectionError:
        return None


def _loaded(models: list[dict]) -> list[str]:
    return [m["id"] for m in models if m.get("loaded") or (m.get("status") or {}).get("value") == "loaded"]


# ---------------------------------------------------------------- the work

def count_work(c: dict, since: str | None) -> dict:
    with db() as d:
        w = {"emails": d.execute("SELECT COUNT(*) AS n FROM email_items WHERE status = 'pending'").fetchone()["n"],
             "uploads": d.execute("SELECT COUNT(*) AS n FROM upload_queue WHERE status = 'waiting'").fetchone()["n"]}
        if c["categorize"]:
            w["uncategorized"] = d.execute(
                "SELECT COUNT(*) AS n FROM transactions WHERE category = 'Uncategorized' AND created_at > ?",
                (since or "",)).fetchone()["n"]
    return w


async def _categorize_new(since: str | None) -> dict:
    from .categorize import categorize_rows
    with db() as d:
        rows = d.execute("SELECT id, date, amount, currency, description, counterparty FROM transactions "
                         "WHERE category = 'Uncategorized' AND created_at > ? ORDER BY date DESC LIMIT 400",
                         (since or "",)).fetchall()
    if not rows:
        return {"checked": 0, "categorized": 0}
    warnings = await categorize_rows(rows)
    changed = [r for r in rows if r["category"] != "Uncategorized"]
    with db() as d:
        d.executemany("UPDATE transactions SET category = ?, category_source = ? WHERE id = ?",
                      [(r["category"], r["category_source"], r["id"]) for r in changed])
    return {"checked": len(rows), "categorized": len(changed), "warnings": warnings}


def _check_time(deadline: float):
    if STATE["cancel"]:
        raise Stop("cancelled", "Stopped by you")
    if time.monotonic() > deadline:
        raise Stop("timeout", "Maximum run time reached - stopping")


async def run_cycle(trigger: str = "manual") -> dict:
    if STATE["running"]:
        return {"busy": True}
    from . import mail, upload_queue
    c = cfg()
    prev = last_run(ok_only=True)
    since = prev["started_at"] if prev else None      # "new" transactions = added after the last good run
    with db() as d:
        rid = d.execute("INSERT INTO desktop_runs(trigger, started_at) VALUES (?, ?)", (trigger, now())).lastrowid
    STATE.update(running=True, run_id=rid, cancel=False, phase="Starting")
    t0 = time.monotonic()
    deadline = t0 + c["max_run"] * 60
    status, woke, loaded_by_us, done = "ok", False, None, {}
    try:
        # 1. email scan here on the server, then: is there anything for the model?
        if mail.accounts(enabled_only=True) and not mail.STATE["running"]:
            _log("Scanning email inboxes")
            await mail.run(read=False)
        work = count_work(c, since)
        _save(work=work)
        _log("Waiting for the model: " + ", ".join(f"{v} {k}" for k, v in work.items()))
        if c["only_if_work"] and not any(work.values()):
            raise Stop("nothing to do", "Nothing to do - the PC is not woken")

        # 2. the PC and Studio
        models = await studio_models()
        if models is None:
            if await machine_up(c):
                _log("The PC is on, but Unsloth Studio does not answer yet - waiting for it")
            else:
                if not c["mac"]:
                    raise Stop("error", "No Wake-on-LAN MAC address set")
                _log(f"Waking the PC (Wake-on-LAN to {c['mac']})")
                send_wol(c["mac"])
                woke, woke_at = True, time.monotonic()
                _save(woke=1)
            wait_until, last_wol = time.monotonic() + c["boot_wait"] * 60, time.monotonic()
            while models is None:
                _check_time(deadline)
                if time.monotonic() > wait_until:
                    if await windows_up(c):
                        raise Stop("error", "The PC started Windows, not Linux - set Linux as the default boot entry")
                    raise Stop("error", f"Unsloth Studio did not answer within {c['boot_wait']:g} minutes "
                                        "(is it started automatically when Linux boots?)")
                await asyncio.sleep(10)
                if woke and time.monotonic() - last_wol > 60 and not await machine_up(c):
                    send_wol(c["mac"])                      # magic packets get lost; repeat while it is dark
                    last_wol = time.monotonic()
                models = await studio_models()
            _log("Unsloth Studio answers" + (f" ({time.monotonic() - woke_at:.0f} s after waking)" if woke else ""))
        else:
            _log("The PC is already on and Unsloth Studio answers")

        # 3. the model
        loaded = _loaded(models)
        if loaded:
            _log(f"Using the model that is already loaded: {loaded[0]}")
        else:
            if not c["model"]:
                raise Stop("error", "No model is loaded and none is set to load")
            _log(f"Loading {c['model']}")
            await studio("POST", "/models/load", {"model": c["model"]}, timeout=1200)
            loaded_by_us = c["model"]
            _log("Model loaded")
        if c["ocr_mode"] != "off":
            from . import ocr
            for _ in range(12):                              # the OCR server may start a bit after Studio
                if (await ocr.status()).get("ok"):
                    break
                await asyncio.sleep(10)
            else:
                _log("OCR server not answering - photos are read by the main model only")

        # 4. the work
        _check_time(deadline)
        if work.get("uploads"):
            _log(f"Reading {work['uploads']} queued receipt upload(s)")
            done["uploads"] = await upload_queue.process(limit=500)
        _check_time(deadline)
        if work.get("emails"):
            _log(f"Reading {work['emails']} email document(s)")
            for _ in range(60):                              # the email scheduler may be busy for a moment
                if not mail.STATE["running"]:
                    break
                await asyncio.sleep(5)
            mail.STATE.update(running=True, phase="Reading documents with the model (desktop automation)")
            try:
                done["emails"] = await mail.read_all_pending()
            finally:
                mail.STATE.update(running=False, phase="", finished=now())
        _check_time(deadline)
        if work.get("uncategorized"):
            _log(f"Categorizing {work['uncategorized']} new transaction(s)")
            done["categorize"] = await _categorize_new(since)
        _save(done=done)
        _log("Work done: " + json.dumps(done, default=str)[:400])
    except Stop as e:
        status = e.status
        _log(str(e))
    except Exception as e:
        status = "error"
        log.exception("desktop run")
        _log(f"Error: {type(e).__name__}: {e}")
    finally:
        STATE["phase"] = "Finishing"
        shutdown = ""
        try:
            shutdown = await _finish(c, woke, loaded_by_us)
        except Exception as e:
            shutdown = f"error: {e}"
            _log(f"Error while finishing: {e}")
        _save(status=status, finished_at=now(), shutdown=shutdown, done=done)
        STATE.update(running=False, run_id=None, phase="", cancel=False)
    return {"run_id": rid, "status": status}


async def _finish(c, woke: bool, loaded_by_us: str | None) -> str:
    """Leave the PC as it was found: power off what we woke (if allowed and nobody is at it), otherwise
    unload what we loaded."""
    result, power_off = "", False
    if woke and not c["shutdown"]:
        _log("Shutting down is turned off - the PC stays on")
        result = "off in settings"
    elif woke:
        ok, out = await ssh("status")
        if not ok:
            if await windows_up(c):
                _log("Cannot power off: the PC is running Windows")
                result = "failed: running Windows"
            else:
                _log(f"Cannot power off - SSH to the desktop failed ({out}). Run the one-time setup on the desktop.")
                result = "failed: SSH not set up"
        elif c["respect_users"] and parse_status(out)["in_use"]:
            # A session that logged in automatically after the wake counts as "in use" until the desktop marks it
            # idle (GNOME: after the screen-blank delay, 5 min by default). Give it that long before deciding.
            _log("A session on the PC is active - waiting up to 10 minutes for it to go idle")
            in_use = True
            for _ in range(20):
                await asyncio.sleep(30)
                ok, out = await ssh("status")
                if ok and not parse_status(out)["in_use"]:
                    in_use = False
                    break
            if in_use:
                _log("Someone is using the PC - leaving it on")
                result = "skipped: PC in use"
            else:
                power_off = True
        else:
            power_off = True
    if loaded_by_us and not power_off:
        try:
            await studio("POST", "/models/unload", {"model": loaded_by_us}, timeout=120)
            _log(f"Unloaded {loaded_by_us}")
        except Exception as e:
            _log(f"Could not unload the model: {e}")
    if power_off:
        ok, out = await ssh("poweroff")
        _log("The PC powers off in 2 minutes" if ok else f"Power-off failed: {out}")
        result = "in 2 minutes" if ok else f"failed: {out[:200]}"
    return result


async def test() -> dict:
    """Settings 'Test': can we reach Studio, and does the SSH status/power-off access work?"""
    c = cfg()
    try:
        models = await studio_models()
    except Exception as e:
        models, err = None, str(e)
    else:
        err = None if models is not None else "Studio does not answer"
    ok, out = await ssh("status")
    res = {"studio": models is not None, "studio_error": err, "loaded": _loaded(models) if models else [],
           "machine_up": await machine_up(c), "windows": await windows_up(c), "ssh": ok}
    if ok:
        res["status"] = parse_status(out)
    else:
        res["ssh_error"] = out
    return res


async def scheduler():
    """Background loop in the web app: start a run when one is due."""
    with db() as d:   # a run cut off by an app restart
        d.execute("UPDATE desktop_runs SET status = 'interrupted', finished_at = ?, "
                  "log = log || ? WHERE status = 'running'", (now(), f"{datetime.now():%H:%M:%S} Interrupted (the app restarted)\n"))
    await asyncio.sleep(90)
    while True:
        try:
            nr = next_run()
            if nr and datetime.now() >= nr and not STATE["running"]:
                await run_cycle("schedule")
        except Exception:
            log.exception("desktop scheduler")
        await asyncio.sleep(60)


# ---------------------------------------------------------------- one-time setup on the desktop

REMOTE = r'''#!/bin/bash
# Called by the Local Ledger server over SSH as a forced command: these are the only things its key can do.
case "$SSH_ORIGINAL_COMMAND" in
  status)
    echo "boot $(uptime -s)"
    echo "grub $(grep -s '^GRUB_DEFAULT=' /etc/default/grub | cut -d= -f2-) $(grub-editenv list 2>/dev/null | grep saved_entry | tr '\n' ' ')"
    for s in $(loginctl list-sessions --no-legend | awk '{print $1}'); do
      echo "session $(loginctl show-session "$s" -p Name -p Class -p Type -p Active -p IdleHint -p Remote | paste -sd' ')"
    done ;;
  poweroff)
    /usr/sbin/shutdown -h +2 "Local Ledger has finished - this PC powers off in 2 minutes. Keep it on: sudo shutdown -c"
    echo scheduled ;;
  cancel)
    /usr/sbin/shutdown -c && echo cancelled ;;
  *)
    echo "not allowed: $SSH_ORIGINAL_COMMAND" >&2; exit 1 ;;
esac
'''


def setup_script() -> str:
    pub = ensure_key()
    src = ",".join(_local_ips()) or "SERVER-IP"
    return f'''#!/bin/bash
# Local Ledger desktop automation - one-time setup on the desktop's Linux. Run:  sudo bash ledger-desktop-setup.sh
# It lets the ledger server ({src}) ask this PC for its status and power it off - nothing else:
# the key below may only run /usr/local/sbin/ledger-remote, which knows three commands: status, poweroff, cancel.
set -euo pipefail
[ "$(id -u)" = 0 ] || {{ echo "Please run with sudo"; exit 1; }}

# 1. SSH server, keys only (password logins stay off for everyone; root only with a forced command)
command -v sshd >/dev/null || {{ apt-get update && apt-get install -y openssh-server; }}
mkdir -p /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/00-local-ledger.conf <<'EOF'
PermitRootLogin forced-commands-only
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF

# 2. the only commands the key can run
cat > /usr/local/sbin/ledger-remote <<'EOF'
{REMOTE.strip()}
EOF
chmod 755 /usr/local/sbin/ledger-remote

# 3. the ledger server's key, usable only from the server and only for ledger-remote
install -d -m 700 /root/.ssh
touch /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys
sed -i '/local-ledger-desktop/d' /root/.ssh/authorized_keys
echo 'restrict,command="/usr/local/sbin/ledger-remote",from="{src}" {pub}' >> /root/.ssh/authorized_keys

systemctl enable --now ssh 2>/dev/null || systemctl enable --now sshd
systemctl restart ssh 2>/dev/null || systemctl restart sshd

# 4. firewall: SSH only from the ledger server
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  for ip in {src.replace(",", " ")}; do ufw allow from "$ip" to any port 22 proto tcp comment "Local Ledger"; done
fi

# 5. keep Wake-on-LAN armed after Linux powers off (NetworkManager)
if command -v nmcli >/dev/null; then
  nmcli -t -f NAME,TYPE connection show --active | awk -F: '$2 ~ /ethernet/ {{print $1}}' | while read -r con; do
    nmcli connection modify "$con" 802-3-ethernet.wake-on-lan magic && echo "Wake-on-LAN (magic packet) kept on for: $con"
  done
fi
# ...and switch it on right now (NetworkManager only applies the setting when the connection next comes up)
command -v ethtool >/dev/null || apt-get install -y ethtool
for dev in $(ls /sys/class/net); do
  [ -e "/sys/class/net/$dev/device" ] || continue
  if ethtool "$dev" 2>/dev/null | grep -q "Supports Wake-on:.*g"; then
    ethtool -s "$dev" wol g && echo "Wake-on-LAN on now for $dev: $(ethtool "$dev" | grep -E '^[[:space:]]*Wake-on:' | xargs)"
  fi
done

echo
echo "Done. Press Test in the ledger's Settings > Desktop automation."
echo "Also check: Linux is the default boot entry, Unsloth Studio (and the OCR server) start on their own"
echo "after boot, and Wake-on-LAN is enabled in the BIOS."
'''
