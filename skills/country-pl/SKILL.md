---
name: country-pl
description: Poland - the local vocabulary for Polish books in PLN: what paragony, faktury and bank lines call things, typical Polish payees, VAT rules. Added automatically to the other bookkeeping skills when the ledger's country is Poland.
---
# Poland

Each section below is added to the skill with the same subject when the books are Polish.

## Basics
- Polish złoty is written "zł" or "PLN". Numbers look like 1 234,56 or 1.234,56; dates DD.MM.YYYY or YYYY-MM-DD.
- A card payment abroad shows the foreign amount in the bank text, e.g. "21,00 USD kurs 4,0312".
- Cash is "gotówka"; VAT is "VAT" (on receipts "PTU"); the accountant is the "księgowa" / "biuro rachunkowe".

## Receipts
- A till receipt is a "paragon fiskalny". The total is "SUMA PLN", "SUMA", "RAZEM" or "DO ZAPŁATY". "Rabat" / "Opust"
  lines are discounts; "Gotówka" is the cash handed over and "Reszta" is the change - neither is the total.
- VAT is printed per letter: "PTU A 23%", "PTU B 8%", "PTU C 5%", "PTU D 0%", with "Sprzedaż opodatkowana A" as the
  gross amount of that rate and "Suma PTU" as the VAT total. Use the printed figures.
- The seller's number is "NIP" (10 digits, often written 123-456-78-90). A receipt with the buyer's NIP ("NIP
  nabywcy") counts as a simplified invoice up to 450 zł.
- Payment: "Karta", "Karta płatnicza", "Visa", "Mastercard", "zbliżeniowo" -> card; BLIK, Apple Pay, Google Pay ->
  mobile; "Gotówka" -> cash; "Przelew" -> transfer; PayPo, Klarna, Twisto -> other.
- Fuel at Orlen / BP / Shell / Circle K / Moya -> Fuel & vehicle; food bought there -> Meals & entertainment.

## Invoices
- An invoice is a "Faktura" / "Faktura VAT"; a correction is a "Faktura korygująca" (credit note); a "Faktura
  pro forma" is not an invoice yet - document_type 'other'.
- The number is "Nr faktury" / "Faktura nr". Dates: "Data wystawienia" (issue), "Data sprzedaży" / "Data dostawy"
  (delivery), "Termin płatności" (due).
- Amounts: "Wartość netto" / "Razem netto" (net), "Kwota VAT" / "Razem VAT" (VAT), "Wartość brutto" / "Razem brutto"
  / "Do zapłaty" (total). "Zapłacono" means it has been paid; "Pozostało do zapłaty" is what is still owed.
- Reverse charge is "odwrotne obciążenie"; "mechanizm podzielonej płatności" (split payment) is only a payment
  method note - the amounts stay as printed.
- Tax IDs: NIP (10 digits), "PL" + NIP as the EU VAT number, sometimes REGON. Payment details are "Nr rachunku" /
  IBAN.

## Bank statements
- Line types: "Zakup przy użyciu karty" / "Płatność kartą" / "Transakcja kartą" (card), BLIK, "Przelew wychodzący" /
  "Przelew przychodzący" (transfer out / in), "Przelew na telefon" (phone transfer), "Zlecenie stałe" (standing
  order), "Polecenie zapłaty" (direct debit), "Wypłata z bankomatu" (ATM), "Prowizja" / "Opłata" (fee), "Odsetki"
  (interest), "Wynagrodzenie" (salary).
- Not transactions: "Saldo początkowe", "Saldo końcowe", "Saldo po operacji" as a separate summary line, and blocked
  amounts ("Blokada", "Środki zablokowane").
- Columns: "Data operacji" / "Data transakcji" and "Data księgowania" (booking date - use this one), "Data waluty"
  (value date), "Opis operacji" / "Tytuł" / "Szczegóły", "Nadawca" / "Odbiorca" / "Kontrahent", "Kwota" (one signed
  amount) or "Obciążenia" (out) and "Uznania" (in), "Saldo po operacji", "Waluta". The separator is usually ";".
  Older exports are encoded in Windows-1250.

## Categorization
- Refunds are "Zwrot" or "Zwrot środków". Own transfers: "Przelew własny", "Przelew na konto oszczędnościowe",
  "Lokata". Loan payments: "Rata kredytu", "Spłata kredytu", "Spłata karty". ATM: "Wypłata z bankomatu".
- People pay each other with BLIK: "Przelew na telefon" or a transfer to or from a person's name is friends and
  family.
- Pay-later is PayPo, Klarna, Twisto or "Allegro Pay".
- Public money IN: "800+" / "Rodzina 800+", zasiłek, świadczenie from ZUS, stypendium, "Zwrot podatku" (PIT refund).
  Money OUT to ZUS, Urząd Skarbowy (PIT, VAT, CIT), KRUS is tax and social security.
- Salary is "Wynagrodzenie" / "Pensja".

## Categorization payees (reading)
- Supermarkets: Biedronka, Lidl, Żabka, Kaufland, Auchan, Carrefour, Dino, Stokrotka, Netto, Aldi, Lewiatan.
- Orange, T-Mobile, Play, Plus, UPC, Vectra, Netia -> Telecom & internet; PGE, Tauron, Enea, Energa, PGNiG ->
  Utilities; PZU, Warta, Ergo Hestia, Link4 -> Insurance; "Czynsz" -> Rent.
- PKP Intercity, Koleje, Jakdojade, MPK / ZTM tickets, Bolt, Uber, FreeNow, LOT -> Travel. Orlen, Moya, parking,
  e-TOLL -> Fuel & vehicle.
- Apteka, Rossmann, Hebe, Medicover, LuxMed, dentysta -> Health & personal.
- Allegro, Empik, Pepco, CCC, Reserved, Decathlon, Vinted, OLX; parcels with InPost / Paczkomat, DPD, DHL ->
  Shopping & clothing. Media Expert, RTV Euro AGD, x-kom, Leroy Merlin, Castorama -> Equipment & hardware.
- Pyszne.pl, Glovo, Wolt, Uber Eats, restauracja, pizzeria -> Meals & entertainment.
- STS, Fortuna, Lotto, Cinema City, Multikino, Helios -> Leisure, games & hobbies.
- XTB, Zonda, Binance, "Obligacje skarbowe" -> Investments & crypto.

## Reconciliation
- ATM withdrawals appear as "Wypłata z bankomatu"; the cash book is the account "Gotówka".
- A business keeps its invoices and receipts for 5 years, counted from the end of the year in which the tax was due.

## VAT and tax
- Output VAT is "VAT należny", input VAT "VAT naliczony"; the return is filed as JPK_V7M (monthly) or JPK_V7K
  (quarterly, small taxpayers) with the Urząd Skarbowy.
- Rates: 23% standard, 8% and 5% reduced, 0% for some supplies. Exempt ("zw."): healthcare, education, financial and
  insurance services.
- Deduction limits: no VAT deduction on restaurant and hotel services; 50% for passenger cars that are also used
  privately (fuel and running costs included), 100% only for cars used solely for the business.
- Polish VAT rules change often - present every figure as an estimate for the accountant.
