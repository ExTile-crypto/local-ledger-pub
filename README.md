# Local Ledger

A small bookkeeping app you host yourself. It reads **bank statements, receipts and invoices** with a language model
of your choice, categorizes what you spend, matches receipts to bank lines, and answers questions about your money.
Your books stay in one folder on your own machine.

- Works with any **OpenAI-compatible model server**: Unsloth Studio, LM Studio, Ollama, llama.cpp on your own
  computer - or a cloud API with your own key if you have no GPU.
- **Private or business** books, any base currency, other currencies converted at ECB rates.
- **Country notes** for Denmark and Poland (what receipts and bank lines are called there, typical shops, VAT);
  other countries use the general rules, and adding a country is one text file.
- No build step, one SQLite file, runs in Docker or as a plain Python app.

## Quick start (Docker)

```bash
git clone <this repository> local-ledger && cd local-ledger
cp .env.example .env          # set LEDGER_PASSWORD if anyone else can reach the machine
docker compose up -d
```

Open http://127.0.0.1:8890. The first start shows a setup screen: whose books these are (private / business,
country, currency) and which model reads your documents, with a **Test** button that tells you what the model can do
(structured answers, reading images, tool calling).

Your books live in `./data` - back that folder up. Updating: `git pull && docker compose up -d --build`.

A model server on the same computer is reached from the container as `http://host.docker.internal:<port>/v1`
(the setup screen fills that in).

## Quick start (without Docker)

Python 3.11+ and, for scanned PDFs, `pdftoppm` (package `poppler-utils`).

```bash
./run.sh                                         # http://127.0.0.1:8890
HOST=0.0.0.0 LEDGER_PASSWORD=choose-one ./run.sh  # reachable from other devices, with a password
```

To run it as a service, see `deploy/local-ledger.service.example`.

## Choosing a model

| | What you need | Privacy |
|---|---|---|
| **Local** (Unsloth Studio, LM Studio, Ollama, llama.cpp) | A GPU (or patience). A **vision** model for photos of receipts; a model with tool calling for the Assistant. | Nothing leaves your machine. |
| **Cloud API** (OpenAI, OpenRouter, any OpenAI-compatible service) | An API key. | Every document you upload and the bank lines you categorize are sent to that provider. |

Notes:
- The app adapts to the server by itself: if a server rejects a parameter or does not enforce JSON schemas, the client
  notices from the server's answer, adjusts, and remembers it (Settings → Model → Test shows what it found).
- With a **text-only** model, digital PDFs, emails and bank exports still work; photos need the optional OCR server
  (below) or a vision model.
- Without a model you can still browse the ledger, categorize by rules and keywords, reconcile and build reports.
  Importing a statement needs the model (it works out the column layout), and receipt uploads wait in a queue until
  a model answers.
- Quality depends on the model. `tests/check_prompts.py` runs the bundled sample documents through your model and
  prints PASS/FAIL per field - run it once after choosing a model.

## Who can open it

There are no user accounts: one ledger, one owner. Keep it on your own computer or a private network (a VPN such as
Tailscale), or set `LEDGER_PASSWORD`. With a password, browsers sign in once; scripts, phone shortcuts and MCP clients
send `Authorization: Bearer <password>`. Put it behind HTTPS (a reverse proxy) before exposing it to the internet.

## Configuration

Installation settings are environment variables (`.env` with Docker) - see `.env.example`:
`LEDGER_PASSWORD`, `LEDGER_URL`, `LEDGER_HOSTS`, `LEDGER_DATA_DIR`, and optional presets for the setup screen
(`LEDGER_COUNTRY`, `LEDGER_BASE_CURRENCY`, `LEDGER_LLM_BASE_URL`, ...).
Everything about the books is set in the app under **Settings**.

## Sections

| Section | What it does |
|---|---|
| **Overview** | "Needs attention" list (documents waiting for the model, receipts to review, uncategorized lines), income, spending, what is left, money moved between accounts; charts; account balances. |
| **Assistant** | Chat with the model. It looks things up with tools, categorizes, reconciles, and builds Excel or CSV files. |
| **Transactions** | Search and filter, change categories (remembered per payee), own exchange rates, export. Auto-categorize: uncategorized lines; also re-check the catch-all categories; or rules and keywords only (no model needed). |
| **Bank statements** | CSV / Excel / PDF / scans / MT940 / CAMT. For CSV and Excel the model works out the column layout and Python parses every row exactly; PDFs and scans are read by the model and checked against the balances. Preview, then import; duplicates are skipped and an import can be undone. |
| **Cash** | The cash book: receipts paid in cash are booked here; record ATM withdrawals and cash expenses. |
| **Receipts** | Photos or PDFs, or from your email inbox. Merchant, date, total, VAT, line items, payment method; matched to the bank transaction. Uploads wait in a queue while the model is offline. |
| **Invoices** | Purchase and sales invoices, due dates, paid / unpaid / overdue. |
| **Reconcile** | Suggested matches, expenses without a document, receipts without a bank line, cards per account. |
| **Reports** | Excel overview workbook with charts and live formulas, plus generated files. |
| **Settings** | Your books, Model, Accounts & cards, Categories & rules, Email, Automation, Prompts, Studio. |

Sidebar on wide screens; bottom bar and card layout on phones. "Hide amounts" masks every figure for screen sharing.

## Countries

Settings → Your books → Country picks a **country pack** (`app/packs.py`): currency, the cash book's name, the encodings
of local bank exports, keywords for well-known local payees, and a **country skill** with the local vocabulary
(`skills/country-dk`, `skills/country-pl`). Its sections are added to the other skills automatically - "Receipts" to
receipt reading, "Bank statements" to statement import, and so on.

Another country works with the general rules. To add one properly: an entry in `PACKS` and a
`skills/country-<id>/SKILL.md` with the same section headings as the existing ones.

## Optional: OCR server

`run_ocr.sh` starts PaddleOCR-VL (a small OCR model) on llama.cpp. With it, every photo is read twice - the OCR
transcription and the main model - and amounts that disagree are flagged and re-read. Settings → Model → OCR.
Digital PDFs and emails do not need it: their own text is used as the second reading.

## Optional: wake a GPU PC for the work

Settings → Automation: if the model runs on a PC you do not keep on, the ledger can wake it over the LAN every few
days, wait for the model server (and load the model when it is Unsloth Studio), read what is waiting, and power the
PC off again - only if it woke it and nobody is using it.

## Receipts from email

Settings → **Email inboxes** connects mailboxes over IMAP (Gmail, iCloud, Yahoo, Fastmail or any IMAP server; Outlook /
Hotmail are not supported yet because Microsoft only allows OAuth sign-in). Gmail, iCloud and Yahoo need an *app password*
(Gmail: https://myaccount.google.com/apppasswords, requires 2-Step Verification).

- Every few hours (Settings, default 6 h) and on *Scan now*, new emails are checked. Candidates are emails with receipt
  words in the subject or sender (receipt, invoice, order confirmation, kvittering, faktura, paragon, Rechnung, ...) and emails
  with PDF or photo attachments.
- The model first decides whether it really is a receipt/invoice (newsletters and ads are skipped), then reads
  the PDF, photo or the email text itself (e.g. Wolt, App Store). Results go into Receipts or Invoices, are matched to
  bank lines, and cash receipts go into the cash book. The original email (.eml) is kept as documentation.
- **Read-only**: folders are opened read-only and messages fetched without marking them as read; nothing is moved or
  deleted. Each email is processed once, also if it arrives in two inboxes.
- If the model is offline, found emails wait and are read automatically later. The same happens when a text-only
  model is loaded: scans and photos wait for a vision model, text documents are read meanwhile. A document the model
  cannot handle is marked as an error (Retry in the list) and no longer blocks the ones behind it.
- Mailbox passwords (and the model API key) are stored in the app's database and are never sent back to a browser.
- Receipts → *Receipts from your email* shows progress and each email's result (retry / skip).

## Currencies

- The **base currency** is chosen in the setup screen (changeable in Settings; everything is recalculated if you change it).
- Every transaction, receipt and invoice keeps its original amount and currency **plus** the base-currency amount, converted
  with the ECB euro reference rate of that date (the last business day before weekends and holidays). Rates are
  downloaded once from the ECB and cached in `data/fx/`. This is the only thing the app downloads; your data stays local.
- Card payments abroad: the original amount in the bank text (e.g. `Visa køb USD 21,00 ... Kurs 6,6706`) is captured,
  so a foreign receipt is matched to its bank line exactly. Otherwise the converted amounts must agree within 3%
  and the names must match.
- You can override a rate on any transaction ("rate" button) or receipt. Manual rates are kept.
- In the Excel overview, the base-currency columns are formulas (`amount × rate`), so correcting a rate in Excel updates all totals.

## Skills and prompts

The model's instructions are plain text files, `skills/<name>/SKILL.md` (the Agent Skills format): `bookkeeper-core`
(always), `bank-statement-import`, `receipt-extraction`, `invoice-management`, `transaction-categorization`,
`reconciliation`, `financial-overviews`, `vat-and-tax`, and the country skills.

- A prompt is: a one-line role, `bookkeeper-core`, the skill for the job (each followed by its country section), your
  books (type, owner, your name on bank lines, accounts, categories) and the task.
- A section whose heading ends with `(chat)` is only sent to the Assistant, one ending with `(reading)` only when
  documents are read or lines categorized.
- Categorization asks the model once per **payee**, not once per line, and tells it how that payee is categorized
  already. Order: your rules → keywords → what you chose before → the model.
- Reading is checked twice: a photo against the OCR text, a digital document against its own text.
- Edit skills in **Settings → Prompts**. Your version is saved in `data/skills` and wins over the built-in file, so an
  update never overwrites it; "Reset to built-in" removes it. "Preview full prompt" shows the exact text and its size.

## Tools (Assistant and MCP)

`app/tools.py` is the single definition; the in-app Assistant and the MCP server (`/mcp`) use the same functions and
descriptions: `ledger_overview`, `run_sql`, `search_transactions`, `list_uncategorized`, `set_category` (by ids or for
a whole payee, with `remember`), `auto_categorize`, `add_transactions`, `save_receipt`, `save_invoice`,
`missing_documents`, `reconcile`, `link_document`, `convert_currency`, `create_overview_workbook`,
`create_custom_workbook`, `export_csv` (+ `list_categories` over MCP).

- SQL written by the model is read-only and cannot read the `settings`, `email_accounts`, `email_items` or
  `desktop_runs` tables (the model API key and mailbox passwords live there).
- Unknown category names are stored as Uncategorized with a warning; `save_receipt` / `save_invoice` report a duplicate
  instead of saving the same document twice.

## Using the ledger from another chat app

The app is an MCP tool server at `<address>/mcp`; any MCP-capable chat app can use the same tools. For Unsloth Studio,
Settings → Studio registers the tools with one click and offers the skills as a zip for `~/.agents/skills`.

## Tests

- `tests/check_prompts.py` - the bundled samples against your real model, PASS/FAIL per field, writes nothing.
- `tests/fake_llm.py` - a stand-in model server for dry runs; it can imitate strict APIs, text-only models and
  servers without JSON-schema support (see its docstring).

## Files

```
app/        Python backend (FastAPI)          skills/   the model's instructions
static/     web UI (no build step)            tests/    prompt check, stand-in model server
samples/    sample statements and receipts    deploy/   systemd unit example
data/       YOUR BOOKS: ledger.db, uploads, reports, edited skills   <- back this up, never in git
```

## Notes

- VAT figures are estimates for an accountant; deduction limits are flagged, not applied.
- Old `.xls` files are not supported - save them as `.xlsx` or CSV.
- This is a personal tool, not accounting software with an audit trail. Check what the model read: every receipt
  starts in "review" until you confirm it.
