---
name: financial-overviews
description: Build financial overviews and reports from the ledger in its base currency - Excel workbooks with charts, monthly income and spending, category breakdowns, currency summaries, CSV exports. Use when the user asks for a report, overview, summary, chart, spreadsheet or export.
---
# Financial overviews

## Pick the right output
| The user wants | Use |
|---|---|
| A quick answer in chat | `ledger_overview` or `run_sql`, answer with a small table |
| The usual period overview (income, spending, categories, VAT, missing documents) | `create_overview_workbook(start_date, end_date)` |
| A specific analysis (fuel per month, top shops, subscriptions, one counterparty, foreign-currency spend, year over year) | `create_custom_workbook` with one SQL SELECT per sheet |
| Data for an accountant or another program | `export_csv` |

Answer in chat by default. Build a file when the user asks for Excel, a spreadsheet, a chart, an export or something
to keep or send.

## Getting the numbers right
- Sum the base-currency columns: `amount_base` (transactions), `total_base` / `vat_base` (receipts, invoices). Never
  sum `amount` across currencies; show the original currency in its own column when it is useful.
- Income and spending exclude transfers: join categories and keep kind IN ('income','expense'). A refund sits in an
  expense category with a positive amount, so `-SUM(amount_base)` over an expense category is already net of refunds.
- Uncategorized lines (kind 'other') are in neither income nor spending - say how many there are in the period.
- Clean column aliases: `substr(date,1,7) AS month`, `ROUND(-SUM(amount_base),2) AS spent`. Show spending as positive
  numbers and say so.

## Designing a custom workbook
- One idea per sheet; plain sheet names ("Fuel by month", "Top shops").
- `totals: true` for tables that should sum. A chart only when it helps: column = months, bar = ranking,
  line = trend or cumulative, pie only for 5 parts or fewer.
- Put the period, the currency, the conversion method (ECB rate of the transaction date) and what is excluded in
  `notes`.

## Useful queries
- Monthly income and spending: `SELECT substr(t.date,1,7) AS month, ROUND(SUM(CASE WHEN k.kind='income' THEN t.amount_base ELSE 0 END),2) AS income, ROUND(-SUM(CASE WHEN k.kind='expense' THEN t.amount_base ELSE 0 END),2) AS spent FROM transactions t JOIN categories k ON k.name=t.category WHERE t.date BETWEEN '2026-01-01' AND '2026-12-31' GROUP BY month ORDER BY month`
- Spending per category: `SELECT t.category, COUNT(*) AS lines, ROUND(-SUM(t.amount_base),2) AS spent FROM transactions t JOIN categories k ON k.name=t.category WHERE k.kind='expense' AND t.date BETWEEN '2026-01-01' AND '2026-12-31' GROUP BY t.category ORDER BY spent DESC`
- Top counterparties: `SELECT counterparty, COUNT(*) AS payments, ROUND(-SUM(amount_base),2) AS spent FROM transactions WHERE amount<0 AND date >= '2026-01-01' GROUP BY counterparty ORDER BY spent DESC LIMIT 20`
- Recurring payments (subscriptions): `SELECT counterparty, COUNT(DISTINCT substr(date,1,7)) AS months, ROUND(-AVG(amount_base),2) AS typical, MAX(date) AS last_paid FROM transactions WHERE amount<0 AND date >= date('now','-12 months') GROUP BY counterparty HAVING months >= 3 ORDER BY typical DESC`
- Foreign-currency spend: `SELECT COALESCE(NULLIF(orig_currency,''), currency) AS currency, COUNT(*) AS n, ROUND(SUM(COALESCE(orig_amount, amount)),2) AS original, ROUND(SUM(amount_base),2) AS in_base_currency FROM transactions WHERE orig_currency != '' OR fx_source != 'same' GROUP BY 1`

## After creating a file
Report the key figures (income, spending, net or the main finding), the period and currency, anything that weakens
the result (uncategorized lines, missing documents, missing exchange rates), and the download link.
