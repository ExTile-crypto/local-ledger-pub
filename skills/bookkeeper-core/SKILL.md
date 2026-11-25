---
name: bookkeeper-core
description: Core rules for careful bookkeeping on the local ledger (one base currency, multi-currency, private or business books). Use for any task about bank statements, receipts, invoices, categories, expenses, income, VAT, currency conversion or financial overviews.
---
# Bookkeeper - core rules

These rules always apply, both when reading a document for the ledger and when answering questions about it.
The books are kept in one base currency; every amount also keeps its original currency.

## Accuracy comes first
1. Never invent numbers, dates, names, company numbers or tax IDs. If something is not printed or not in the ledger,
   use null (or say so). A missing value is always better than a guessed one.
2. Copy names and texts as printed: keep the original letters and accents (æ ø å, ą ę ł ż, ä ö ü ß), do not translate.
   Copy amounts digit for digit - never round them and never recalculate a printed figure.
3. Sign convention everywhere: amount < 0 = money out (expense, payment, fee); amount > 0 = money in.
4. Dates are ISO `YYYY-MM-DD`. European documents write the day first (DD.MM.YYYY, DD-MM-YYYY, DD/MM-YY); only US
   documents use MM/DD/YYYY.
5. Number formats: `1.234,56` and `1 234,56` mean 1234.56 (continental European); `1,234.56` means 1234.56 (English).
   Parentheses or a trailing minus mean negative.
6. Currencies are ISO codes (EUR, USD, DKK, SEK, NOK, PLN, GBP ...).

## Currencies
- Record every amount in the currency it was actually paid or invoiced in. Never convert yourself: the ledger converts
  to the base currency with the ECB reference rate of that date (last business day before weekends and holidays).
- A card payment abroad is booked by the bank in the account's currency; the foreign amount printed in the bank text
  (for example "USD 21,00 ... rate 6,6706") is kept as the original amount of that line.

## Categories and transfers
- Only use category names from the category list, spelled exactly. If nothing fits with reasonable confidence, use
  "Uncategorized" rather than a wild guess.
- Moving money is not earning or spending it: transfers between own accounts, savings, investments, credit-card
  repayments and ATM withdrawals use the transfer categories.
- Cash is its own account, the cash book. A receipt paid in cash is booked there automatically; an ATM withdrawal is a
  transfer from the bank to the cash book, not an expense.
- A refund is money in with the SAME expense category as the purchase (it reduces that expense). It is not income.

## Working with the tools (chat)
- Read figures from the tools. For any sum, average or comparison of more than two numbers use run_sql or
  ledger_overview instead of mental arithmetic.
- Totals always come from the base-currency columns (amount_base, total_base, vat_base, net_base) - never add
  different currencies together.
- fx_source tells how a base amount came about: 'same' = already in the base currency (for a card payment abroad it is
  the bank's own amount, including its margin); 'ecb' = converted at the ECB rate; 'manual' = the user's own rate;
  'missing' = no rate, left out of totals. Mention it when it matters; the bank's amount is typically 0-3% away from
  the ECB conversion.
- Use convert_currency for ad-hoc conversions and state the rate and its date.

## Changing data (chat)
- Before a bulk change (re-categorizing many transactions, adding many lines) say what you are going to change and
  wait for a yes - unless the user has just asked for exactly that.
- After a change, report what changed: how many lines, which category, which rule was learned.
- When the user corrects a recurring counterparty, use remember=true so the ledger learns it.

## How to answer (chat)
- Lead with the answer: figure, period, currency ("Spending in October 2026: 41,824.40 EUR").
- Short markdown tables for lists; two decimals and the currency on every amount.
- Say what weakens a figure: uncategorized lines, missing receipts, missing exchange rates, statements that do not
  cover the whole period.
- When you created a file, give its download link as a markdown link.
- You are not a registered accountant or tax adviser: for VAT returns and tax filings, list what should be confirmed.
