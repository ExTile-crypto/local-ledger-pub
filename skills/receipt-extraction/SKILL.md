---
name: receipt-extraction
description: Read receipts and till slips (photos, scans, PDFs and email receipts, in any language) - merchant, company number, date, total, VAT per rate, line items, payment method, currency - and store them. Use when the user shares a receipt or asks to book a receipt.
---
# Receipt extraction

## The numbers
- total = what was actually paid, after discounts and including delivery and fees: the line labelled Total, Sum,
  Amount due or To pay (TOTAL, Summe, Gesamt, Totalt, Att betala, TOTAAL, Total TTC, Suma, Razem ...).
  It is NOT the subtotal before discounts, not the cash handed over, not the change given back, not a card
  pre-authorisation and not loyalty points.
- VAT: use the VAT amount that is printed. If no VAT is printed, vat is null; do not calculate it.
  One vat_lines entry per rate; net = gross - VAT. Usual standard and reduced rates: 25% (DK, SE, NO), 19%/7% (DE),
  23%/8%/5% (PL), 20% (UK, FR, AT), 21%/9% (NL), 12%/6% (SE reduced), 15% (NO reduced).
- items: description, quantity, unit price and line total as printed. Discount lines are negative items.
- currency: the symbol or code that is printed. "kr" is DKK, SEK or NOK depending on the country (check the address
  and the VAT ID prefix). € -> EUR, £ -> GBP, zł -> PLN, $ -> USD unless the document says otherwise.

## Who and when
- merchant: the trading name of the seller, usually at the top. Not the payment terminal or payment provider (Nets,
  Worldline, Stripe, Adyen, PayPal, Klarna) when a shop name is shown.
  If no store name is printed (logo cut off), do NOT guess it from promotions, loyalty programmes or ads: use what is
  printed, e.g. the town and the company number, and say so in notes.
- merchant_tax_id: the seller's VAT or company registration number as printed.
- date: the purchase or payment date. For a subscription or app-store receipt it is the date of the charge, not the
  billing period.

## How it was paid
- payment_method: card (debit or credit card, contactless), mobile (Apple Pay, Google Pay, a phone payment app), cash,
  transfer (bank transfer, direct debit), other (gift card, PayPal balance, pay-later), unknown when nothing is
  printed.
- card_last4: the last four card digits if printed ("XXXX XXXX XXXX 1234", "Visa •••• 1234"). They decide which
  account the receipt belongs to, so read them carefully. A receipt that shows card digits was paid by card, not cash.
- Cash: when the receipt shows cash handed over and change given back, payment_method is cash and total is the amount
  due, not the cash handed over. Cash receipts go into the cash book automatically, so getting this right matters.

## Email receipts and order confirmations
- The receipt can be the email text itself (app stores, food delivery, ride hailing, subscriptions). Use the order
  total that was charged; ignore "you saved", recommended products and footer text.
- When an email lists several payments or a payment plan, record the amount charged now and mention the plan in notes.
- A document for 0.00 (free trial, fully paid by gift card or credit) has total 0.

## Category
Choose by what was bought, not only by the shop: fuel at a petrol station -> Fuel & vehicle, but a sandwich bought
there -> Meals & entertainment. Mixed receipt: the category of the largest part, and mention the mix in notes.

## Quality
- Blurry, cut off or unreadable total -> total null, confidence 'low', and say in notes what could not be read.
- confidence 'high' only when merchant, date and total are all clearly printed; 'medium' when you had to interpret.
- If it is really an invoice (an invoice number, buyer details, payment terms or a due date) set document_type
  'invoice'.

## Saving a receipt from a chat (chat)
Show the fields you read as a short table and ask only when something important is unreadable. Then call
`save_receipt` in the ORIGINAL currency, with card_last4 when it is printed. The receipt is converted to the base
currency and matched to its bank transaction automatically (cash receipts go to the cash book) - report the result.
