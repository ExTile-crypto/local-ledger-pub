---
name: country-dk
description: Denmark - the local vocabulary for Danish books in DKK: what receipts, invoices and bank lines call things, typical Danish payees, moms (VAT) rules. Added automatically to the other bookkeeping skills when the ledger's country is Denmark.
---
# Denmark

Each section below is added to the skill with the same subject when the books are Danish.

## Basics
- Danish kroner are written "kr.", "DKK" or with ",-" (as in "499,-"). Numbers look like 1.234,56; dates DD.MM.YYYY or
  DD-MM-YYYY.
- A card payment abroad shows the foreign amount in the bank text: "Visa køb USD 21,00 GITHUB.COM Kurs 6,6706".
- Cash is "kontanter"; VAT is "moms"; the accountant is the "revisor".

## Receipts
- The total is "I alt", "Total", "At betale", "Beløb", "Sum" or "Betalt". "Rabat" lines are discounts; "Modtaget" /
  "Kontant" is the cash handed over and "Byttepenge" / "Retur" is the change - neither is the total.
- VAT is printed as "Moms 25%", "Heraf moms" or "Moms udgør", sometimes with "Momsgrundlag" (the net amount). Denmark
  has one rate, 25%, so the printed moms is 20% of the total incl. moms - use that only to check your reading.
- The seller's number is "CVR" / "CVR-nr." (8 digits). The date is often "Dato" next to "Kl." (the time).
- Payment: Dankort, Visa/Dankort, Visa, Mastercard, "kontaktløs" -> card; MobilePay -> mobile; "Kontant" -> cash;
  Betalingsservice -> transfer; ViaBill or Klarna pay-later -> other.
- "Kontantnota" / "Kontantbon" only means "paid on the spot" - look for the real means of payment before choosing
  cash.
- Fuel at Circle K / Q8 / OK / Uno-X -> Fuel & vehicle; food bought there -> Meals & entertainment.

## Invoices
- An invoice is a "Faktura"; a credit note a "Kreditnota". The number is "Fakturanr." / "Faktura nr.".
- Dates: "Fakturadato" (issue), "Leveringsdato" (delivery), "Forfaldsdato" / "Betalingsfrist" (due). Terms such as
  "Netto 8 dage", "Netto 14 dage", "Løbende måned + 30 dage".
- Amounts: "Beløb ekskl. moms" / "Subtotal" (net), "Moms 25%" (VAT), "I alt inkl. moms" / "Total DKK" / "At betale"
  (total). "Betalt" means it has been paid.
- Reverse charge is "Omvendt betalingspligt".
- Tax IDs: CVR (8 digits), and "DK" + CVR as the VAT number. Payment details are "Reg.nr." + "Kontonr.", IBAN, or a
  FIK line (+71<...>).

## Bank statements
- Line types: Dankort-nota, Visa/Dankort, Mastercard (card), MobilePay, Overførsel (transfer), Betalingsservice / BS
  (direct debit), Løn (salary), Gebyr (fee), Renter (interest), Hævning (ATM).
- Not transactions: Primo / Ultimo saldo, "Saldo pr. ...", Overført saldo, Saldo transport, and reserved amounts
  (Reserveret, status "Venter").
- Columns: Dato / Bogføringsdato / Bogført (booking date), Rentedato / Valør (value date), Tekst / Beskrivelse /
  Posteringstekst, Beløb (one signed amount), or Hævet / Udbetaling / Debet (out) and Indsat / Indbetaling / Kredit
  (in), Saldo, Valuta, Modtager, Afsender, Navn, Status, Afstemt. The separator is usually ";".
- The counterparty of a card payment follows the prefix: "Dankort-nota NETTO NØRREBRO 4521" -> "Netto Nørrebro";
  "MobilePay Jens Hansen" -> "Jens Hansen".

## Categorization
- Refunds are "Tilbageførsel" or "Retur". Own transfers: "Egen overførsel", "Overførsel til opsparing". Loan
  principal: "Afdrag", "Lån". ATM: "Hævning i automat", "Kontanthævning".
- People pay each other with MobilePay: "MobilePay <name>" or a bare person's name is friends and family; a bare
  "MobilePay" with no name stays Uncategorized.
- Pay-later is ViaBill, Klarna or Anyday.
- Public money IN: SU, boligstøtte, børne- og ungeydelse, dagpenge, feriepenge, "Overskydende skat". Money OUT to
  Skattestyrelsen / SKAT / Gældsstyrelsen, B-skat, restskat, moms, ATP, AM-bidrag, Motorstyrelsen is tax.
- Salary is "Løn"; "Lønudbetaling" paid by a business is Salaries & contractors.

## Categorization payees (reading)
- Supermarkets: Netto, Føtex, Bilka, Rema 1000, Lidl, Coop 365, Kvickly, SuperBrugsen, Meny, Aldi.
- Betalingsservice bills: TDC / YouSee / Telenor / Telia / Norlys / 3 / CBB / Lebara -> Telecom & internet;
  Ørsted / Andel Energi / HOFOR / Radius / Evida -> Utilities; Tryg / Topdanmark / Alm. Brand / Codan / Gjensidige ->
  Insurance.
- DSB, Rejsekort, Movia, Metro -> Travel. EasyPark, Brobizz, Storebælt, Øresundsbron -> Fuel & vehicle.
- Apotek, tandlæge, læge, Matas -> Health & personal. Bank plans such as Lunar Plus / Lunar Standard -> Bank fees.
- Normal, Søstrene Grene, Imerco, Jysk, Magasin; parcels with GLS, DAO, PostNord -> Shopping & clothing.
- Danske Spil, Mr Green -> Leisure, games & hobbies. one.com, Simply.com, e-conomic, Dinero, Billy ->
  Software & subscriptions.

## Reconciliation
- ATM withdrawals appear as "Hævning i automat" or "Kontanthævning"; the cash book is the account "Kontanter".
- Business books follow bogføringsloven: every business transaction needs a bilag (receipt or invoice), kept for 5
  years (digitally for businesses covered by the digital bookkeeping rules).

## VAT and tax
- Output VAT is "salgsmoms", input VAT "købsmoms"; the return is the "momsangivelse", filed with Skattestyrelsen.
- The standard rate is 25% and there are no reduced rates. Exempt (momsfritaget): passenger transport (bus, train,
  taxi), insurance, financial services, healthcare.
- Deduction limits: restaurant meals for business - normally only 25% of the moms; entertainment and gifts
  (repræsentation) - generally none; passenger cars on white plates - generally none, vans on yellow plates differ.
- Small businesses usually file half-yearly, larger ones quarterly or monthly.
- German MwSt on a receipt from Flensburg, or any other foreign VAT, is not deductible in the Danish return.
