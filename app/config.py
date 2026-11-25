"""Paths and default settings."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("LEDGER_DATA_DIR") or BASE_DIR / "data")  # override for tests / other installs
UPLOAD_DIR = DATA_DIR / "uploads"
REPORT_DIR = DATA_DIR / "reports"
DB_PATH = DATA_DIR / "ledger.db"
SKILLS_DIR = BASE_DIR / "skills"               # built-in skills
USER_SKILLS_DIR = DATA_DIR / "skills"          # skills edited in the app: these win over the built-in ones
STATIC_DIR = BASE_DIR / "static"

for d in (DATA_DIR, UPLOAD_DIR, REPORT_DIR):
    d.mkdir(parents=True, exist_ok=True)

DEFAULT_SETTINGS = {
    # --- the model: any OpenAI-compatible endpoint (Unsloth Studio, LM Studio, Ollama, llama.cpp, a cloud API...)
    "llm_provider": "",             # preset chosen in the UI (app/llm.py PRESETS); informational
    "llm_base_url": "http://127.0.0.1:8888/v1",
    "llm_fallback_url": "",         # tried when the first address cannot be reached (e.g. LAN address of the same PC)
    "llm_model": "",                # empty = the one model the server has loaded / offers
    "llm_api_key": "",
    "llm_vision": "auto",           # auto | yes | no - 'no': photos and scans are only read through the OCR server
    "llm_doc_reasoning": "off",     # reasoning when reading documents: off | low | medium | xhigh
                                    # 'off' + checks is fastest; a document that fails the checks is re-read with xhigh
    "llm_thinking_chat": "1",
    "llm_quirks": "",               # what this server does not accept, learned from its error messages (app/llm.py)
    # --- whose books
    "company_name": "",
    "books_type": "private",    # private | business | mixed - tells the model whose money this is (app/skills.py)
    "owner_names": "",          # how the owner is named on bank lines, comma separated: such lines are own transfers
    "country": "",              # free text, shown to the model
    "country_pack": "",         # dk | pl | generic (app/packs.py); empty = worked out from `country`
    "base_currency": "EUR",
    "vat_registered": "0",
    # --- optional OCR pass (run_ocr.sh) and inbox scanning
    "ocr_url": "",
    "ocr_mode": "off",    # off | both (image + OCR text to the model) | text (OCR text only, faster)
    "email_scan_hours": "6",   # scan email inboxes for receipts every N hours (0 = only when you press Scan now)
    "receipt_match_days": "5",
    "missing_doc_threshold": "0",
    # --- desktop automation (app/desktop.py): wake a GPU PC, load the model, do the model work, power it off
    "desktop_auto": "0",                 # 1 = run on the schedule below
    "desktop_every_days": "3",
    "desktop_time": "03:40",             # local time of day
    "desktop_model": "",                 # loaded through Unsloth Studio's API when nothing is loaded
    "desktop_only_if_work": "1",         # don't wake the PC when nothing is waiting for the model
    "desktop_categorize": "1",           # also categorize new Uncategorized transactions
    "desktop_shutdown": "1",             # power off afterwards - only when this automation woke the PC
    "desktop_respect_users": "1",        # ...and nobody is using it
    "desktop_boot_wait_min": "10",       # how long to wait for the model server after waking
    "desktop_max_run_min": "90",         # stop working (and shut down) after this long
    "desktop_mac": "",                   # Wake-on-LAN address of the PC
    "desktop_hosts": "",                 # its addresses, comma separated (SSH + checks)
}

# Categories a private person's books need and a business chart of accounts lacks. An older database gets them once
# (db.init_db, flag 'household_categories_added'), so deleting one sticks.
HOUSEHOLD = ("Shopping & clothing", "Leisure, games & hobbies", "Benefits & public transfers", "Investments & crypto",
             "Friends & family")


# Deployment: any default above can be preset with an environment variable LEDGER_<NAME>, e.g.
# LEDGER_LLM_BASE_URL=http://192.168.1.50:1234/v1 or LEDGER_COUNTRY=Denmark (only used when the database is first
# created; afterwards the Settings page is the place). See .env.example for the full list.
import os as _os  # noqa: E402
for _k in list(DEFAULT_SETTINGS):
    _v = _os.environ.get("LEDGER_" + _k.upper())
    if _v is not None:
        DEFAULT_SETTINGS[_k] = _v
