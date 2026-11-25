---
name: transaction-categorization
description: Assign bookkeeping categories to bank transactions, receipts and invoices (private and business books), and learn from corrections. Use when categorizing, re-categorizing or reviewing uncategorized transactions.
---
# Transaction categorization

## How to decide
1. Who is the counterparty? A card payment carries the merchant in the text, after the bank's own prefix. Then look
   at the text, then at direction and amount.
2. Direction: money OUT is an expense or a transfer - never an income category. Money IN is income, a refund or a
   transfer.
3. A refund (money in from a shop, airline or supplier; "Refund", "Return", "Reversal") gets the SAME expense
   category as the purchase. It is not Sales revenue and not Other income.
4. Moving money is a transfer, not income or an expense:
   - between the owner's own accounts, savings, wallets and cards (Revolut, PayPal top-up, the owner's own name as
     counterparty) -> Transfer between own accounts;
   - to or from investment and crypto platforms (brokers, a robo portfolio, Coinbase, Binance, MoonPay) ->
     Investments & crypto if the list has it, else Transfer between own accounts. Selling an investment ("Sold
     Solana") is not income;
   - credit-card payoff and loan principal -> Loan & credit card repayment;
   - ATM withdrawals -> Transfer between own accounts (they go to the cash book).
5. People: a payment-app or bank transfer to or from a private person (a person's name as counterparty) is money
   between friends and family - splitting a bill, paying someone back, a gift. Use Friends & family if the list has
   it, else Uncategorized. Do not book it as Sales revenue or as an expense, unless the text says what it was for
   ("rent" -> Rent). A bare payment-app line with no name and no shop stays Uncategorized.
6. Buy now, pay later (Klarna, Afterpay and local equivalents): these payments ARE the purchase being paid. When the
   text names the shop ("Klarna* Elgiganten") use that shop's category; a bare pay-later line is Shopping & clothing
   if the list has it, else Other expenses. It is not a loan repayment.
7. Public payments coming IN (student grant, housing support, child benefit, unemployment benefit, tax refund) ->
   Benefits & public transfers if the list has it, else Other income. Payments OUT to the tax or social-security
   authority -> Taxes & social security.
8. Salary received -> Salary received. Sales revenue is only for customers paying the owner's business for goods or
   services; private books normally have none.
9. If two categories fit, take the more specific one. "Other expenses" / "Other income" are a last resort for
   something you understand but that has no category; "Uncategorized" is for what you cannot tell.

## Typical payees (reading)
- Supermarkets and grocery chains -> Groceries & household.
- Phone, mobile, internet and TV providers -> Telecom & internet; electricity, heating, water, gas -> Utilities;
  insurers -> Insurance.
- Trains, public transport, flights, hotels, taxis and ride hailing -> Travel. Fuel, EV charging, parking, tolls ->
  Fuel & vehicle.
- Pharmacy, dentist, doctor, optician, hairdresser, gym and fitness -> Health & personal.
- The bank's own charges - account or plan fees (Revolut Premium and the like), card fees, interest paid -> Bank fees.
- Apps, cloud, AI and software subscriptions (Apple, Google, Microsoft, Adobe, GitHub, OpenAI, Anthropic) ->
  Software & subscriptions.
- Clothes, shoes, cosmetics, home goods and general web shops (H&M, Zalando, Nike, Amazon) and parcel fees (DHL, GLS,
  DPD) -> Shopping & clothing if the list has it, else Other expenses.
- Betting and lottery, games (Steam, Blizzard, PlayStation), streaming, cinema, concerts, hobbies ->
  Leisure, games & hobbies if the list has it, else Other expenses.

## Consistency (reading)
The same counterparty gets the same category every time. When a line says how it was categorized before
("before: ..."), keep that category unless it is clearly wrong for this line - for example a refund, or an income
category on money out.

## Foreign currency
The category does not depend on the currency: a USD GitHub payment is still Software & subscriptions.

## Learning (chat)
When the user corrects the category of a recurring counterparty, call set_category with remember=true so future
imports follow it, and mention the rule that was learned.

## Review routine (chat)
- A first pass over everything uncategorized: auto_categorize (rules, keywords, then the model in batches).
- Reviewing what is left: list_uncategorized -> propose a category per counterparty in a table -> after the user
  agrees, set_category once per counterparty (counterparty="...", remember=true) instead of listing ids.
