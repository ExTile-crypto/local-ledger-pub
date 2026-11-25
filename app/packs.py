"""Country packs: what differs from one country to the next.

A pack supplies the currency, the name of the cash book, the text encodings of local bank exports, the keywords that
categorize well-known local payees without asking the model, and the name of a "country skill"
(skills/country-<id>/SKILL.md) with the local vocabulary the model needs: what a total is called on a receipt, how
bank lines are worded, which shops are supermarkets, how VAT works.

Adding a country = one entry in PACKS plus one skills/country-<id>/SKILL.md. Everything else is shared.
The category NAMES are the same everywhere (the skills refer to them); keywords and descriptions are per pack.
"""

# name, kind (income | expense | transfer | other), neutral description
CHART = [
    ("Sales revenue", "income", "Payments from customers for goods or services"),
    ("Other income", "income", "Grants, sold belongings, miscellaneous income"),
    ("Interest income", "income", "Interest paid by the bank"),
    ("Salary received", "income", "Wages or salary paid to you by an employer"),
    ("Benefits & public transfers", "income", "Public benefits, student grants, housing support, child benefit, tax refunds"),
    ("Cost of goods & materials", "expense", "Stock, raw materials, goods for resale"),
    ("Office supplies", "expense", "Stationery, printer ink, small office items"),
    ("Software & subscriptions", "expense", "SaaS, cloud, apps, hosting, domains"),
    ("Telecom & internet", "expense", "Phone, mobile plan, internet, TV"),
    ("Rent", "expense", "Rent for a home, office or workspace"),
    ("Utilities", "expense", "Electricity, heating, water, gas"),
    ("Travel", "expense", "Train, flights, hotels, taxis, public transport"),
    ("Fuel & vehicle", "expense", "Fuel, EV charging, parking, tolls, car service"),
    ("Meals & entertainment", "expense", "Restaurants, cafés, take-away, catering, client meals"),
    ("Groceries & household", "expense", "Supermarkets, household shopping (usually private)"),
    ("Professional services", "expense", "Accountant, lawyer, consultants"),
    ("Bank fees", "expense", "Account fees, card fees, transfer fees, FX fees, interest paid"),
    ("Insurance", "expense", "Home, vehicle, liability and other insurance"),
    ("Marketing & advertising", "expense", "Ads, promotion, printing, sponsorship"),
    ("Equipment & hardware", "expense", "Computers, phones, tools, furniture"),
    ("Salaries & contractors", "expense", "Payroll, freelancers, subcontractors"),
    ("Taxes & social security", "expense", "Income tax, VAT payments, social security, vehicle tax"),
    ("Education & training", "expense", "Courses, books, conferences"),
    ("Health & personal", "expense", "Medical, pharmacy, personal care (usually private)"),
    ("Shopping & clothing", "expense", "Clothes, shoes, cosmetics, home goods, general web shops, parcel fees, pay-later instalments (usually private)"),
    ("Leisure, games & hobbies", "expense", "Betting and lottery, games, streaming, cinema, concerts, sports and hobbies"),
    ("Other expenses", "expense", "Anything that fits nowhere else"),
    ("Transfer between own accounts", "transfer", "Moving money between your own accounts or cards, savings, cash withdrawals"),
    ("Owner deposit / withdrawal", "transfer", "The owner of a business putting money in or taking it out"),
    ("Loan & credit card repayment", "transfer", "Principal repayments of loans or credit cards"),
    ("Investments & crypto", "transfer", "Money moved to or from investment, savings-robot and crypto platforms (buying and selling is not income or spending)"),
    ("Friends & family", "transfer", "Transfers to or from private persons: splitting bills, paying back, gifts"),
    ("Uncategorized", "other", "Not yet categorized - needs review"),
]

# payees and words that mean the same thing everywhere
GLOBAL = {
    "Sales revenue": "invoice",
    "Interest income": "interest",
    "Salary received": "salary, payroll",
    "Software & subscriptions": "github, google workspace, google cloud, microsoft, adobe, openai, anthropic, notion, slack, aws, amazon web services, hetzner, digitalocean, dropbox, atlassian, jetbrains, figma, zoom, shopify, cloudflare",
    "Rent": "rent",
    "Travel": "ryanair, easyjet, wizz air, flixbus, uber, bolt, booking.com, airbnb, hotel",
    "Fuel & vehicle": "circle k, shell, easypark, parking",
    "Meals & entertainment": "restaurant, café, cafe, mcdonald, burger king, kfc, starbucks, wolt, uber eats, just eat",
    "Groceries & household": "lidl, aldi, carrefour, spar",
    "Professional services": "accountant, lawyer",
    "Bank fees": "fee, card fee",
    "Insurance": "insurance",
    "Marketing & advertising": "google ads, facebook ads, meta platforms, linkedin, mailchimp",
    "Equipment & hardware": "apple store, ikea",
    "Salaries & contractors": "freelance",
    "Education & training": "udemy, coursera, conference",
    "Health & personal": "pharmacy, dentist",
    "Shopping & clothing": "hennes & mauritz, h&m, zalando, asos, nike, temu, shein, aliexpress",
    "Leisure, games & hobbies": "steam, steamgames, blizzard, playstation, nintendo, netflix, spotify, ticketmaster",
    "Transfer between own accounts": "own transfer, savings, atm, cash withdrawal",
    "Loan & credit card repayment": "repayment",
    "Investments & crypto": "coinbase, binance, etoro, revolut digital assets",
}

PACKS = {
    "dk": {
        "name": "Denmark", "match": ("denmark", "danmark", "dk"), "currency": "DKK", "cash_account": "Kontanter",
        "encodings": ("utf-8-sig", "cp1252", "latin-1"), "skill": "country-dk",
        "descriptions": {
            "Sales revenue": "Payments from customers for goods or services (salg)",
            "Interest income": "Interest paid by the bank (renteindtægt)",
            "Salary received": "Wages/salary paid to you by an employer (løn)",
            "Benefits & public transfers": "SU, boligstøtte, børne- og ungeydelse, dagpenge, feriepenge, tax refund (overskydende skat)",
            "Cost of goods & materials": "Stock, raw materials, goods for resale (varekøb)",
            "Office supplies": "Stationery, printer ink, small office items (kontorartikler)",
            "Rent": "Rent for a home, office or workspace (husleje)",
            "Utilities": "Electricity, heating, water (el, varme, vand)",
            "Travel": "Train, flights, hotels, taxis, public transport (rejser)",
            "Fuel & vehicle": "Fuel, EV charging, parking, tolls, car service (brændstof, parkering)",
            "Meals & entertainment": "Restaurants, cafés, catering, client meals (repræsentation)",
            "Professional services": "Accountant, lawyer, consultants (revisor, advokat)",
            "Bank fees": "Account fees, card fees, transfer fees, FX fees, interest paid (gebyr, renteudgift)",
            "Insurance": "Home, vehicle, liability insurance (forsikring)",
            "Equipment & hardware": "Computers, phones, tools, furniture (inventar)",
            "Salaries & contractors": "Payroll, freelancers, subcontractors (lønudbetaling, honorar)",
            "Taxes & social security": "Moms, B-skat, A-skat, AM-bidrag, ATP, vehicle tax",
            "Transfer between own accounts": "Moving money between your own accounts or cards (opsparing)",
            "Owner deposit / withdrawal": "Owner putting money in or taking it out (privat hævning / indskud)",
            "Loan & credit card repayment": "Principal repayments of loans or credit cards (afdrag)",
            "Friends & family": "MobilePay and transfers to or from private persons: splitting bills, paying back, gifts",
        },
        "keywords": {
            "Sales revenue": "faktura, invoice, indbetaling faktura",
            "Interest income": "renter, rente, interest, renteindtægt",
            "Salary received": "løn, salary, lønoverførsel",
            "Benefits & public transfers": "su, boligstøtte, børne- og ungeydelse, dagpenge, feriepenge, overskydende skat, udbetaling danmark",
            "Office supplies": "lyreco, staples, kontorland",
            "Software & subscriptions": "github, google workspace, google cloud, microsoft, adobe, openai, anthropic, notion, slack, aws, amazon web services, hetzner, digitalocean, dropbox, atlassian, jetbrains, figma, zoom, e-conomic, dinero, billy, shopify, one.com, simply.com, cloudflare",
            "Telecom & internet": "tdc, yousee, telenor, telia, norlys, stofa, hiper, cbb, oister, lebara",
            "Rent": "husleje, lejemål, rent",
            "Utilities": "ørsted, andel energi, hofor, radius, evida, fjernvarme, vandværk, ewii, nrgi",
            "Travel": "dsb, rejsekort, movia, metro, sas, norwegian, ryanair, flixbus, dantaxi, taxa, uber, bolt, booking.com, airbnb, hotel",
            "Fuel & vehicle": "circle k, q8, shell, ingo, uno-x, f24, goon, clever, easypark, apcoa, parkering, brobizz, storebælt, øresundsbron, autoværksted",
            "Meals & entertainment": "restaurant, café, cafe, lagkagehuset, joe & the juice, wolt, just eat, mcdonald, sunset boulevard, emmerys, espresso house, starbucks",
            "Groceries & household": "netto, føtex, bilka, rema 1000, lidl, coop, kvickly, superbrugsen, dagli'brugsen, irma, meny, 365discount, løvbjerg, salling",
            "Professional services": "revisor, revision, advokat, bogholder, accountant",
            "Bank fees": "gebyr, kortgebyr, fee, renteudgift, overtræksrente",
            "Insurance": "tryg, topdanmark, alm. brand, codan, gjensidige, if skadeforsikring, lb forsikring, forsikring",
            "Marketing & advertising": "google ads, facebook ads, meta platforms, linkedin, mailchimp",
            "Equipment & hardware": "elgiganten, power, proshop, komplett, apple store, ikea, bauhaus, silvan, jem & fix",
            "Salaries & contractors": "lønudbetaling, honorar, freelance",
            "Taxes & social security": "skattestyrelsen, skat, motorstyrelsen, b-skat, restskat, atp, am-bidrag, moms",
            "Education & training": "udemy, coursera, conference, saxo, arnold busck",
            "Health & personal": "apotek, matas, tandlæge, læge",
            "Shopping & clothing": "hennes & mauritz, h&m, zalando, asos, boozt, nike, søstrene grene, imerco, jysk, notino, temu, shein, magasin",
            "Leisure, games & hobbies": "danske spil, mr green, bet365, unibet, leovegas, steam, steamgames, blizzard, playstation, nintendo, ticketmaster, billetlugen",
            "Transfer between own accounts": "egen overførsel, overførsel til opsparing, opsparing, own transfer, kontanthævning, hævning i automat, pengeautomat, hæveautomat, geldautomat, atm, cash withdrawal",
            "Owner deposit / withdrawal": "privat hævning, privatudtræk, indskud",
            "Loan & credit card repayment": "afdrag, kreditkort, lån, repayment",
            "Investments & crypto": "robo portfolio, bondora, kucoin, moonpay, nordnet, coinbase, binance, etoro, revolut digital assets",
        },
    },
    "pl": {
        "name": "Poland", "match": ("poland", "polska", "pl"), "currency": "PLN", "cash_account": "Gotówka",
        "encodings": ("utf-8-sig", "cp1250", "iso-8859-2"), "skill": "country-pl",
        "descriptions": {
            "Benefits & public transfers": "800+, zasiłki, świadczenia ZUS, stypendia, zwrot podatku (PIT)",
            "Rent": "Rent for a home, office or workspace (czynsz, najem)",
            "Utilities": "Electricity, heating, water, gas (prąd, ogrzewanie, woda, gaz)",
            "Taxes & social security": "PIT, VAT, ZUS, podatek od nieruchomości, vehicle tax",
            "Friends & family": "BLIK na telefon and transfers to or from private persons: splitting bills, paying back, gifts",
        },
        "keywords": {
            "Sales revenue": "faktura, invoice, zapłata za fakturę",
            "Interest income": "odsetki, interest",
            "Salary received": "wynagrodzenie, pensja, salary",
            "Benefits & public transfers": "800+, rodzina 800, zasiłek, zasilek, świadczenie wychowawcze, zwrot podatku, stypendium",
            "Software & subscriptions": GLOBAL["Software & subscriptions"] + ", home.pl, nazwa.pl, ovh",
            "Telecom & internet": "orange polska, t-mobile, polkomtel, p4 sp, upc, vectra, netia, inea, nju mobile, heyah, cyfrowy polsat, canal+",
            "Rent": "czynsz, najem, wynajem, rent",
            "Utilities": "pge, tauron, enea, energa, e.on, pgnig, wodociągi, wodociagi, mpwik, veolia",
            "Travel": "pkp intercity, intercity, koleje, polregio, jakdojade, mpk, ztm, flixbus, uber, bolt, freenow, free now, ryanair, wizz air, wizzair, pll lot, booking.com, airbnb, hotel",
            "Fuel & vehicle": "orlen, lotos, circle k, shell, moya, amic, autostrada, e-toll, parking, skycash, mobilet, myjnia",
            "Meals & entertainment": "restauracja, pizzeria, kawiarnia, mcdonald, kfc, burger king, pyszne.pl, pyszne, glovo, wolt, uber eats, starbucks, costa coffee",
            "Groceries & household": "biedronka, lidl, żabka, zabka, kaufland, auchan, carrefour, dino, stokrotka, netto, aldi, lewiatan, polomarket, intermarche, spar, delikatesy centrum, frisco",
            "Professional services": "biuro rachunkowe, księgowa, ksiegowa, kancelaria, notariusz, adwokat",
            "Bank fees": "prowizja, opłata za prowadzenie, oplata za prowadzenie, opłata za kartę, oplata za karte, opłata miesięczna, odsetki",
            "Insurance": "pzu, warta, ergo hestia, allianz, generali, link4, compensa, uniqa, ubezpieczenie",
            "Marketing & advertising": GLOBAL["Marketing & advertising"],
            "Equipment & hardware": "media expert, rtv euro agd, x-kom, morele, komputronik, apple store, ikea, leroy merlin, castorama, obi, jysk",
            "Taxes & social security": "zus, urząd skarbowy, urzad skarbowy, krus, podatek",
            "Education & training": "udemy, coursera, szkolenie, conference",
            "Health & personal": "apteka, medicover, luxmed, lux med, enel-med, dentysta, stomatolog, przychodnia, rossmann, hebe, super-pharm",
            "Shopping & clothing": "allegro, zalando, empik, pepco, ccc, reserved, sinsay, decathlon, hennes & mauritz, h&m, temu, shein, aliexpress, inpost, paczkomat, vinted, olx",
            "Leisure, games & hobbies": "sts, fortuna, lotto, totalizator sportowy, betclic, superbet, steam, steamgames, blizzard, playstation, nintendo, netflix, spotify, hbo max, cinema city, multikino, helios, ebilet, ticketmaster",
            "Transfer between own accounts": "przelew własny, przelew wlasny, wypłata z bankomatu, wyplata z bankomatu, bankomat, lokata, konto oszczędnościowe, own transfer, atm",
            "Loan & credit card repayment": "rata kredytu, spłata kredytu, splata kredytu, spłata karty, splata karty, rata pożyczki",
            "Investments & crypto": "xtb, binance, zondacrypto, bitbay, coinbase, etoro, revolut digital assets, obligacje skarbowe",
            "Friends & family": "przelew na telefon, blik p2p",
        },
    },
    "generic": {
        "name": "Other country", "match": (), "currency": "EUR", "cash_account": "Cash",
        "encodings": ("utf-8-sig", "cp1252", "latin-1"), "skill": None, "descriptions": {}, "keywords": GLOBAL,
    },
}


def pack_id(settings: dict | None = None) -> str:
    """The pack for these books: the explicit `country_pack` setting, else worked out from the country's name."""
    if settings is None:
        from .db import get_settings
        settings = get_settings()
    chosen = (settings.get("country_pack") or "").strip().lower()
    if chosen in PACKS:
        return chosen
    country = (settings.get("country") or "").strip().lower()
    for pid, p in PACKS.items():
        if country and country in p["match"]:
            return pid
    return "generic"


def pack(settings: dict | None = None) -> dict:
    return PACKS[pack_id(settings)]


def categories_for(pid: str) -> list[tuple]:
    """The starter chart for one country: (name, kind, description, keywords)."""
    p = PACKS.get(pid, PACKS["generic"])
    return [(name, kind, p["descriptions"].get(name, desc), p["keywords"].get(name, "")) for name, kind, desc in CHART]


def public() -> list[dict]:
    return [{"id": pid, "name": p["name"], "currency": p["currency"], "cash_account": p["cash_account"],
             "has_skill": bool(p["skill"])} for pid, p in PACKS.items()]
