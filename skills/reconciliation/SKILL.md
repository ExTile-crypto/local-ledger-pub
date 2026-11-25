---
name: reconciliation
description: Match receipts and invoices to bank transactions (including foreign-currency payments), find expenses without documents and payments without bank lines. Use for reconciliation, missing receipts, unpaid invoices or month-end close.
---
# Reconciliation

## Matching rules
- Receipt <-> bank line: same amount, bank date 0-5 days after the receipt date (card payments post later), merchant
  name similar to the bank text.
- Foreign-currency receipt or invoice paid from an account in another currency: match on the original amount in the
  bank text ("EUR 61,40 ...") when present; otherwise the converted amounts must agree within about 3% AND the name
  must match. Say that it is a converted match.
- Sales invoice <-> money in of the same total after the issue date; purchase invoice <-> money out. The invoice
  number in the transfer text is strong evidence.
- One bank line belongs to one document. Never force a match - leave it for the user when unsure.
- reconcile() links the clear cases and returns the open suggestions; link_document(doc_type, doc_id, transaction_id)
  links one after the user has agreed (transaction_id null unlinks).

## Cards
- account_cards links each payment card (last 4 digits, e.g. "Visa", "Apple Pay iPhone") to an account;
  receipts.card_last4 is the card printed on the receipt. A receipt paid with a linked card is only matched within
  that card's account.
- An unmatched receipt with a linked card usually means that account's statement (or that period) is not imported
  yet - say which account and period is missing. Card digits not linked to any account: ask which account they
  belong to.
- Apple Pay and Google Pay print the phone's or watch's own number, so one account can have several cards.

## Cash
- Receipts with payment method cash are booked in the cash book (the account with kind 'cash') automatically and are
  never matched to a bank line. ATM withdrawals in the bank statement belong in the cash book as cash in, booked as a
  transfer.
- The cash balance is the sum of the cash book and can never be negative - a negative balance means a withdrawal or
  other cash income is missing.

## How much documentation is needed
- Business books: every business expense needs a document (receipt or invoice) and it must be kept for years - the
  list of expenses without a document matters. Present it largest first so the user can collect them.
- Private books: receipts are only worth chasing for warranties, insurance, deductible or reimbursable costs and
  larger purchases. Do not present hundreds of grocery lines as a problem; focus on larger amounts
  (missing_documents with min_amount) and on what the user asks about.

## Month-end checklist
1. All statements for the period imported (first and last date per account; no gaps).
2. reconcile() to auto-link documents, then go through the remaining suggestions.
3. missing_documents(period) - expenses without a document, largest first.
4. Receipts not matched to any bank line: paid in cash, paid from another account or card, or statement not imported.
5. No uncategorized transactions and none without an exchange rate.
6. Opening balance + transactions = closing balance for each account.
7. Then create the overview workbook for the period.
