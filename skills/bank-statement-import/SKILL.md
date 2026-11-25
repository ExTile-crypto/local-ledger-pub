---
name: bank-statement-import
description: Read bank statements (any bank; CSV, Excel, PDF, scans, MT940/CAMT) into ledger transactions with correct dates, signs, currencies and balances. Use when the user provides or pastes a bank statement or account export.
---
# Bank statement import

## What a transaction line is
- One booked movement on the account: card payment, phone payment, transfer, direct debit, standing order, salary,
  fee, interest, ATM withdrawal.
- NOT transactions: opening and closing balance, balance carried forward, page totals, summaries, and reserved or
  pending amounts.

## Fields
- date: the booking date. Use the value date only when nothing else is printed.
- amount: signed, in the ACCOUNT currency. Many exports have one signed amount column (negative = out); others have
  separate debit (money out) and credit (money in) columns.
- currency: the account currency.
- original amount and currency: a card payment abroad shows the foreign amount in its text, e.g.
  "USD 21,00 GITHUB.COM rate 6,6706" -> the booked amount in the account currency, original -21.00 USD.
- counterparty: the merchant, payee or payer. For card payments it is inside the text, after the bank's own prefix
  ("CARD PAYMENT NETTO CITY 4521" -> "Netto City").
- description: the full text as printed.
- reference: end-to-end id, payment id, invoice number or direct-debit reference, when it is separate.
- balance_after: the running balance on that line, if any.

## Column layout (CSV / Excel)
- Some exports start with a few lines of account information before the header row.
- App banks (Revolut, N26, Wise, Lunar) export English headers: Date / Completed Date, Description, Amount, Fee,
  Currency, Balance, Type, State. Use the completed (booked) date; rows with a state such as PENDING or REVERTED are
  not booked transactions - mention them in notes.
- Continental exports usually use ";" with comma decimals ("-1.234,56"); English exports use "," and decimal points.
  Dates DD.MM.YYYY, DD-MM-YYYY or YYYY-MM-DD.

## Checks that catch mistakes
- Running balance: previous balance + amount = this balance. If it only works with the opposite sign, the sign is wrong.
- Opening balance + sum of all amounts = closing balance. If not, a line is missing, duplicated or has a wrong sign.
- Statements may list the newest line first; that does not change any amount.

## Importing a statement from a chat (chat)
When the user pastes or attaches a statement: extract the lines, show a short preview (count, first and last date,
total in and out), then call `add_transactions` with all lines and the account name. Duplicates are skipped and
amounts are converted to the base currency automatically. For long files recommend the Bank statements tab of the
Ledger app.
