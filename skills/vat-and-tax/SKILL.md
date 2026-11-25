---
name: vat-and-tax
description: Estimate VAT (output vs input VAT) and prepare tax-related summaries from receipts and invoices, including foreign-currency documents and cross-border purchases. Use for VAT returns, VAT balance, deductible expenses or tax period summaries.
---
# VAT and tax summaries

## First: do these books file VAT at all?
- Private books, or a business that is not VAT registered: there is no VAT return and nothing to reclaim. Answer a
  VAT question as "VAT paid on purchases" for information only, and say that it cannot be deducted.
- VAT-registered business: the rules below apply.

## The VAT balance
- Output VAT = VAT on sales invoices in the period. Input VAT = VAT on purchase invoices and receipts in the period.
  Balance = output - input; positive = to pay to the tax authority, negative = refund.
- All figures in the base currency: foreign documents are converted with the ECB rate of the document date
  (vat_base / total_base).
- Some supplies are exempt in most countries (passenger transport in some, insurance, financial services,
  healthcare) - a receipt with no VAT there is normal.
- For a period report use create_overview_workbook (its VAT sheet has live formulas). For custom tables query
  receipts.vat_lines / invoices.vat_lines (JSON arrays of {rate, net, vat, gross}) with json_each and multiply by
  fx_rate.

## Deduction limits to FLAG
Do not apply these silently - list them for the accountant. Almost every country limits the deduction for:
- restaurant meals and entertainment;
- gifts and representation;
- passenger cars (purchase, fuel, running costs);
- anything used partly privately (phone, internet, home office): only the business share;
- groceries, health and personal spending are normally private - no deduction.

## Cross-border purchases
- Goods and services bought from another EU country business-to-business, and many services from outside the EU
  (software, cloud), are reverse charge: the invoice has no VAT, and the buyer calculates the local VAT itself, both
  as output and input VAT (usually net zero).
- VAT paid abroad (for example on a receipt from a trip) is NOT deductible in the home return; it can only be
  reclaimed through the refund procedure of that country.

## Always
- Ask which period applies (monthly, quarterly, half-yearly or yearly filing).
- Label the result as an estimate for the accountant, with period and currency, and list the open issues (missing
  documents, uncategorized lines, unpaid invoices, foreign VAT, deduction limits).
