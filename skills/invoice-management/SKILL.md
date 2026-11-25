---
name: invoice-management
description: Read and track sales and purchase invoices and credit notes in any currency - numbers, tax IDs, net/VAT/total, due dates, paid/unpaid, overdue follow-up. Use for invoice documents, receivables, payables or payment status questions.
---
# Invoices

## Reading an invoice
- Seller (issuer) and buyer are both printed. Direction: the owner of the books is normally the BUYER, which makes it
  a **purchase** invoice (money goes out). Only when the owner issued it to a customer is it a **sales** invoice
  (money comes in).
- invoice_number: "Invoice no.", "Rechnungsnummer", "Fakturanr.", "Nr faktury" and the like. An order number is not an
  invoice number - use it only when no invoice number is printed.
- Credit notes ("Credit note", "Gutschrift", "Kreditnota", "Faktura korygująca") reduce amounts: document_type
  credit_note, negative totals.
- Dates: issue date, delivery date, due date. Payment terms such as "Net 14 days" or "End of month + 30 days": compute
  the due date from the issue date and say in notes that it was computed.
- Amounts: net (subtotal excl. VAT), VAT, and the total incl. VAT / amount to pay. Read the summary block, not only
  the line items.
- Reverse charge ("Reverse charge", "Steuerschuldnerschaft des Leistungsempfängers") and EU B2B services show 0 VAT
  on the invoice - record 0 and mention reverse charge in notes.
- Tax IDs: the VAT numbers and company registration numbers of seller and buyer as printed. Bank details (IBAN,
  account number, payment reference) mean it still has to be paid by transfer.
- Currency: record the invoice in the currency printed. The ledger converts it with the ECB rate of the invoice date;
  the actual bank payment may differ slightly (the bank's exchange margin), which is normal.

## Paid or still to pay
- already_paid is true when the document says so ("Paid", "Payment received", charged to a card, PayPal, a phone
  payment app, Klarna) - typical for web-shop and subscription invoices. It is false when the invoice has a due date
  and bank details to pay to.
- An invoice that was already paid by card is booked like a receipt and matched to that card payment.

## Tracking (chat)
- Status is 'unpaid' until a matching bank transaction is linked, then 'paid'. Overdue = unpaid and past its due date.
- Matching: sales invoice <-> incoming payment; purchase invoice <-> outgoing payment. Same currency: same total.
  A foreign invoice paid from an account in another currency: the bank line carries the original amount, or the
  converted amounts agree within about 3%. The invoice number in the payment text is strong evidence. Partial
  payments: note them, do not mark the invoice paid.
- Receivables: run_sql on invoices with direction='sales' and status='unpaid', sorted by due_date, with days overdue
  and total_base.

## Saving an invoice from a chat (chat)
Show the fields you read, then call `save_invoice` with direction, counterparty, issue_date, due_date, number, net,
vat, total, currency and category.
