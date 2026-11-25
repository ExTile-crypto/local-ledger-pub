# Checking the ledger without touching the books

- `check_prompts.py` - runs the bundled sample receipts, invoices and the sample statement through the REAL model and
  compares the answers with what is printed on them, plus a small categorization quiz. Nothing is written to the
  ledger. Run it after editing a skill or switching model:
  `cd /opt/local-ledger && sudo -u ledger .venv/bin/python tests/check_prompts.py`
- `fake_llm.py` - a stand-in for Unsloth Studio (answers with schema-shaped dummy data and records every request in
  `requests.jsonl`). It proves that requests are well-formed and lets you read the exact prompts; it says nothing
  about quality. Use it with a COPY of the data:
  `python tests/fake_llm.py 8898` and start the app with `LEDGER_DATA_DIR=/path/to/copy` after pointing that copy's
  `llm_base_url` setting at `http://127.0.0.1:8898/v1`.
