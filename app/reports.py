"""Excel overviews built with openpyxl.

Totals are live formulas (SUMIFS over the Transactions sheet), so editing a category or amount
in Excel updates every summary. Excel / LibreOffice / Google Sheets recalculate on open.
"""
import csv
import json
import re
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, PieChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

from .config import REPORT_DIR
from .db import db, get_settings, loads, now
from .reconcile import missing_documents

FONT = "Arial"
BLUE, ORANGE = "2A78D6", "EB6834"
HEAD_FILL = PatternFill("solid", fgColor="1F3A5F")
SUB_FILL = PatternFill("solid", fgColor="E8EEF5")
MONEY = '#,##0.00;[Red]-#,##0.00;"-"'
PCT = '0.0%;-0.0%;"-"'
THIN = Side(style="thin", color="B7C3D0")


def _font(**kw):
    return Font(name=FONT, **{"size": 10, **kw})


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")[:60] or "report"


def _to_date(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return s


def _months(start: str, end: str) -> list[str]:
    y, m = int(start[:4]), int(start[5:7])
    ey, em = int(end[:4]), int(end[5:7])
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _style_sheet(ws, widths: dict):
    for col, w in widths.items():
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows():
        for c in row:
            if c.font is None or c.font.name != FONT:
                c.font = _font(bold=c.font.bold if c.font else False, color=c.font.color if c.font else None)


def _header(ws, row: int, labels: list[str], col: int = 1):
    for i, label in enumerate(labels):
        c = ws.cell(row=row, column=col + i, value=label)
        c.font = _font(bold=True, color="FFFFFF")
        c.fill = HEAD_FILL
        c.alignment = Alignment(vertical="center", wrap_text=True)


def _table(ws, name: str, ref: str, style="TableStyleMedium2"):
    t = Table(displayName=name, ref=ref)
    t.tableStyleInfo = TableStyleInfo(name=style, showRowStripes=True)
    ws.add_table(t)


def _title(ws, text: str, sub: str = ""):
    ws["A1"] = text
    ws["A1"].font = _font(size=15, bold=True, color="1F3A5F")
    if sub:
        ws["A2"] = sub
        ws["A2"].font = _font(color="52514E")


def _bar_chart(title, cats_ref, data_refs, colors, horizontal=False, y_title=None):
    ch = BarChart()
    ch.type = "bar" if horizontal else "col"
    ch.grouping = "clustered"
    ch.title = title
    ch.style = 10
    for ref in data_refs:
        ch.add_data(ref, titles_from_data=True)
    ch.set_categories(cats_ref)
    for s, color in zip(ch.series, colors):
        s.graphicalProperties.solidFill = color
        s.graphicalProperties.line.solidFill = color
    ch.gapWidth = 60
    ch.x_axis.delete = False
    ch.y_axis.delete = False
    ch.y_axis.numFmt = "#,##0"
    if y_title:
        ch.y_axis.title = y_title
    if horizontal:
        ch.x_axis.scaling.orientation = "maxMin"  # largest category on top
        ch.legend = None
    ch.height, ch.width = 8.5, 18
    return ch


# ---------------------------------------------------------------- standard overview

def gather(start: str, end: str) -> dict:
    with db() as c:
        tx = c.execute(
            "SELECT t.*, COALESCE(k.kind, 'other') AS kind, COALESCE(a.name, '') AS account, "
            " (SELECT 'Receipt #' || r.id FROM receipts r WHERE r.transaction_id = t.id LIMIT 1) AS rdoc, "
            " (SELECT 'Invoice ' || COALESCE(NULLIF(i.number, ''), '#' || i.id) FROM invoices i WHERE i.transaction_id = t.id LIMIT 1) AS idoc "
            "FROM transactions t LEFT JOIN categories k ON k.name = t.category LEFT JOIN accounts a ON a.id = t.account_id "
            "WHERE t.date BETWEEN ? AND ? ORDER BY t.date, t.id", (start, end)).fetchall()
        rc = c.execute("SELECT * FROM receipts WHERE date BETWEEN ? AND ? AND status != 'rejected' ORDER BY date",
                       (start, end)).fetchall()
        inv = c.execute("SELECT * FROM invoices WHERE issue_date BETWEEN ? AND ? ORDER BY issue_date",
                        (start, end)).fetchall()
        missing = missing_documents(c, start, end, float(get_settings()["missing_doc_threshold"] or 0))
        cash_opening = c.execute(
            "SELECT t.currency, ROUND(SUM(t.amount), 2) AS s FROM transactions t JOIN accounts a ON a.id = t.account_id "
            "WHERE a.kind = 'cash' AND t.date < ? GROUP BY t.currency", (start,)).fetchall()
        cash_rows = c.execute(
            "SELECT t.*, (SELECT 1 FROM receipts r WHERE r.transaction_id = t.id LIMIT 1) AS has_receipt "
            "FROM transactions t JOIN accounts a ON a.id = t.account_id WHERE a.kind = 'cash' AND t.date BETWEEN ? AND ? "
            "ORDER BY t.date, t.id", (start, end)).fetchall()
    return {"tx": tx, "receipts": rc, "invoices": inv, "missing": missing, "cash_opening": cash_opening, "cash": cash_rows}


def period_bounds(start: str | None, end: str | None) -> tuple[str, str]:
    if start and end:
        return start, end
    with db() as c:
        r = c.execute("SELECT MIN(date) AS a, MAX(date) AS b FROM transactions").fetchone()
    today = date.today().isoformat()
    return start or r["a"] or f"{today[:4]}-01-01", end or r["b"] or today


def _fx_formula(amount_cell: str, rate_cell: str) -> str:
    return f'=IF(OR({rate_cell}="",{amount_cell}=""),"",ROUND({amount_cell}*{rate_cell},2))'


def build_overview(start: str | None = None, end: str | None = None, title: str | None = None) -> dict:
    start, end = period_bounds(start, end)
    s = get_settings()
    cur = (s["base_currency"] or "DKK").upper()
    d = gather(start, end)
    tx, months = d["tx"], _months(start, end)
    who = s["company_name"] or "Bookkeeping"
    title = title or f"{who} - overview {start} to {end}"

    wb = Workbook()
    wb.calculation.fullCalcOnLoad = True
    ws_sum = wb.active
    ws_sum.title = "Summary"
    ws_m = wb.create_sheet("Monthly")
    ws_c = wb.create_sheet("Categories")
    ws_x = wb.create_sheet("Category x Month")
    ws_t = wb.create_sheet("Transactions")
    ws_r = wb.create_sheet("Receipts")
    ws_i = wb.create_sheet("Invoices")
    ws_v = wb.create_sheet("VAT")
    ws_md = wb.create_sheet("Missing documents")

    # ---- Transactions (data sheet everything else points at)
    cols = ["Date", "Month", "Account", "Counterparty", "Description", "Category", "Kind", "Amount", "Currency",
            "FX rate", "Rate source", f"Amount ({cur})", "Original amount", "Documented", "Document", "Notes", "ID"]
    _header(ws_t, 1, cols)
    for i, t in enumerate(tx, start=2):
        doc = t["rdoc"] or t["idoc"] or ""
        orig = f"{t['orig_currency']} {t['orig_amount']:,.2f}" if t.get("orig_amount") is not None and t.get("orig_currency") else ""
        vals = [_to_date(t["date"]), t["date"][:7], t["account"], t["counterparty"], t["description"], t["category"],
                t["kind"], t["amount"], t["currency"], t["fx_rate"], t["fx_source"] or "", _fx_formula(f"H{i}", f"J{i}"),
                orig, "Yes" if doc else "No", doc, t["notes"], t["id"]]
        for j, v in enumerate(vals, start=1):
            ws_t.cell(row=i, column=j, value=v)
        ws_t.cell(row=i, column=1).number_format = "yyyy-mm-dd"
        ws_t.cell(row=i, column=8).number_format = MONEY
        ws_t.cell(row=i, column=10).number_format = "0.0000"
        ws_t.cell(row=i, column=12).number_format = MONEY
    n = max(len(tx) + 1, 2)
    if len(tx):
        _table(ws_t, "tblTransactions", f"A1:Q{n}")
    ws_t.freeze_panes = "A2"
    _style_sheet(ws_t, {"A": 11, "B": 9, "C": 16, "D": 26, "E": 46, "F": 26, "G": 10, "H": 13, "I": 9, "J": 9, "K": 11,
                        "L": 14, "M": 15, "N": 11, "O": 14, "P": 18, "Q": 7})
    R = lambda col: f"Transactions!${col}${2}:${col}${n}"  # noqa: E731
    AMT, MON, CAT, KIND, CURR, SRC, DOCD = R("L"), R("B"), R("F"), R("G"), R("I"), R("K"), R("N")

    ni = max(len(d["invoices"]) + 1, 2)
    md_end = max(4 + len(d["missing"]), 5)
    IV = lambda col: f"Invoices!${col}$2:${col}${ni}"  # noqa: E731

    # ---- Summary
    _title(ws_sum, title, f"Period {start} to {end}  ·  generated {now().replace('T', ' ')}")
    ws_sum["A4"], ws_sum["B4"] = "Base currency", cur
    ws_sum["A4"].font = _font(bold=True)
    ws_sum["B4"].font = _font(bold=True)
    ws_sum["C4"] = "All totals are in the base currency. Other currencies are converted with the ECB reference rate of each transaction's date (see Transactions!J)."
    ws_sum["C4"].font = _font(italic=True, color="52514E")
    _header(ws_sum, 6, ["Key figure", f"Amount ({cur})", "How it is calculated"])
    rows = [
        ("Income", f'=SUMIFS({AMT},{KIND},"income")', "Money in, categories of kind 'income'", MONEY),
        ("Expenses", f'=-SUMIFS({AMT},{KIND},"expense")', "Money out, categories of kind 'expense' (refunds reduce it)", MONEY),
        ("Net result", "=B7-B8", "Income - Expenses", MONEY),
        ("Margin", '=IF(B7=0,"",B9/B7)', "Net result / Income", PCT),
        ("Transfers & owner movements (net)", f'=SUMIFS({AMT},{KIND},"transfer")', "Not income or expense - excluded from the result", MONEY),
        ("Uncategorized amount", f'=SUMIFS({AMT},{KIND},"other")', "Needs a category before the books are final", MONEY),
        ("Uncategorized transactions", f'=COUNTIFS({KIND},"other")', "Count", "0"),
        ("Expenses without receipt/invoice", f"=COUNT('Missing documents'!$H$5:$H${md_end})", "Count - see 'Missing documents' (transfers, taxes and bank fees excluded)", "0"),
        ("  ...their total", f"=-SUM('Missing documents'!$G$5:$G${md_end})", "", MONEY),
        ("Output VAT (salgsmoms)", "=VAT!$D$4", "From the VAT sheet", MONEY),
        ("Input VAT (købsmoms)", "=VAT!$C$4", "From the VAT sheet - before any deduction limits", MONEY),
        ("VAT balance (to pay if positive)", "=B16-B17", "Estimate - confirm with your accountant", MONEY),
        ("Open receivables (unpaid sales invoices)", f'=SUMIFS({IV("K")},{IV("A")},"sales",{IV("L")},"unpaid")', "Invoices sheet", MONEY),
        ("Open payables (unpaid purchase invoices)", f'=SUMIFS({IV("K")},{IV("A")},"purchase",{IV("L")},"unpaid")', "Invoices sheet", MONEY),
        ("Transactions converted from other currencies", f'=COUNTIFS({CURR},"<>"&$B$4)', "Included in the totals at the ECB rate", "0"),
        ("Transactions without an exchange rate", f'=COUNTIFS({SRC},"missing")', "NOT included - enter a rate in Transactions!J", "0"),
    ]
    for i, (label, formula, how, fmt) in enumerate(rows, start=7):
        ws_sum.cell(row=i, column=1, value=label)
        c = ws_sum.cell(row=i, column=2, value=formula)
        c.number_format = fmt
        ws_sum.cell(row=i, column=3, value=how).font = _font(color="52514E")
        if label in ("Net result", "VAT balance (to pay if positive)"):
            ws_sum.cell(row=i, column=1).font = _font(bold=True)
            c.font = _font(bold=True)
        for col in range(1, 4):
            ws_sum.cell(row=i, column=col).border = Border(bottom=THIN)
    note_row = 7 + len(rows) + 1
    ws_sum.cell(row=note_row, column=1, value="All figures are formulas over the Transactions sheet: fix a category or exchange rate there and every total updates.").font = _font(italic=True, color="52514E")
    _style_sheet(ws_sum, {"A": 44, "B": 18, "C": 64})

    # ---- Monthly
    _title(ws_m, f"Income and expenses by month ({cur})")
    _header(ws_m, 4, ["Month", "Income", "Expenses", "Net", "Cumulative net"])
    for i, m in enumerate(months, start=5):
        ws_m.cell(row=i, column=1, value=m)
        ws_m.cell(row=i, column=2, value=f'=SUMIFS({AMT},{MON},$A{i},{KIND},"income")')
        ws_m.cell(row=i, column=3, value=f'=-SUMIFS({AMT},{MON},$A{i},{KIND},"expense")')
        ws_m.cell(row=i, column=4, value=f"=B{i}-C{i}")
        ws_m.cell(row=i, column=5, value=f"=D{i}" if i == 5 else f"=E{i - 1}+D{i}")
        for col in range(2, 6):
            ws_m.cell(row=i, column=col).number_format = MONEY
    last = 4 + len(months)
    tot = last + 1
    ws_m.cell(row=tot, column=1, value="Total").font = _font(bold=True)
    for col in range(2, 5):
        L = get_column_letter(col)
        c = ws_m.cell(row=tot, column=col, value=f"=SUM({L}5:{L}{last})")
        c.number_format, c.font, c.border = MONEY, _font(bold=True), Border(top=THIN)
    if months:
        ch = _bar_chart("Income vs expenses per month", Reference(ws_m, min_col=1, min_row=5, max_row=last),
                        [Reference(ws_m, min_col=2, min_row=4, max_row=last),
                         Reference(ws_m, min_col=3, min_row=4, max_row=last)], [BLUE, ORANGE], y_title=cur)
        ws_m.add_chart(ch, "G4")
    _style_sheet(ws_m, {"A": 12, "B": 15, "C": 15, "D": 15, "E": 16})

    # ---- Categories (sorted by size; the sort itself is done in Python)
    totals = {}
    for t in tx:
        if t["amount_base"] is not None:
            totals[(t["kind"], t["category"])] = totals.get((t["kind"], t["category"]), 0) + t["amount_base"]
    _title(ws_c, f"Totals by category ({cur})", "Expenses shown as positive numbers.")
    r = 4
    chart_rows = None
    for kind, sign, label in (("income", "", "Income"), ("expense", "-", "Expenses")):
        names = sorted([k[1] for k in totals if k[0] == kind], key=lambda nm: -abs(totals[(kind, nm)]))
        ws_c.cell(row=r, column=1, value=label).font = _font(bold=True, size=12, color="1F3A5F")
        r += 1
        _header(ws_c, r, ["Category", "Total", "Share", "Transactions"])
        first = r + 1
        for nm in names:
            r += 1
            ws_c.cell(row=r, column=1, value=nm)
            ws_c.cell(row=r, column=2, value=f'={sign}SUMIFS({AMT},{CAT},$A{r})').number_format = MONEY
            ws_c.cell(row=r, column=4, value=f"=COUNTIFS({CAT},$A{r})")
        lastr = r
        r += 1
        ws_c.cell(row=r, column=1, value=f"Total {label.lower()}").font = _font(bold=True)
        ws_c.cell(row=r, column=2, value=f"=SUM(B{first}:B{lastr})" if names else 0).number_format = MONEY
        ws_c.cell(row=r, column=2).font = _font(bold=True)
        for rr in range(first, lastr + 1):
            ws_c.cell(row=rr, column=3, value=f"=IF($B${r}=0,\"\",B{rr}/$B${r})").number_format = PCT
        if kind == "expense" and names:
            chart_rows = (first, lastr)
        r += 3
    if chart_rows:
        ch = _bar_chart("Expenses by category", Reference(ws_c, min_col=1, min_row=chart_rows[0], max_row=chart_rows[1]),
                        [Reference(ws_c, min_col=2, min_row=chart_rows[0] - 1, max_row=chart_rows[1])], [BLUE],
                        horizontal=True)
        ch.height = max(7, 0.6 * (chart_rows[1] - chart_rows[0] + 1) + 3)
        ws_c.add_chart(ch, "F4")
    _style_sheet(ws_c, {"A": 32, "B": 15, "C": 9, "D": 13})

    # ---- Category x Month
    _title(ws_x, f"Category by month ({cur})", "Expenses shown as positive numbers.")
    r = 4
    for kind, sign, label in (("expense", "-", "Expenses"), ("income", "", "Income")):
        names = sorted({k[1] for k in totals if k[0] == kind}, key=lambda nm: -abs(totals[(kind, nm)]))
        if not names:
            continue
        ws_x.cell(row=r, column=1, value=label).font = _font(bold=True, size=12, color="1F3A5F")
        r += 1
        _header(ws_x, r, ["Category"] + months + ["Total"])
        hdr = r
        for nm in names:
            r += 1
            ws_x.cell(row=r, column=1, value=nm)
            for j, m in enumerate(months, start=2):
                L = get_column_letter(j)
                ws_x.cell(row=r, column=j, value=f'={sign}SUMIFS({AMT},{CAT},$A{r},{MON},{L}${hdr})').number_format = MONEY
            ws_x.cell(row=r, column=len(months) + 2,
                      value=f"=SUM(B{r}:{get_column_letter(len(months) + 1)}{r})").number_format = MONEY
        r += 1
        ws_x.cell(row=r, column=1, value="Total").font = _font(bold=True)
        for j in range(2, len(months) + 3):
            L = get_column_letter(j)
            c = ws_x.cell(row=r, column=j, value=f"=SUM({L}{hdr + 1}:{L}{r - 1})")
            c.number_format, c.font = MONEY, _font(bold=True)
        r += 3
    ws_x.freeze_panes = "B4"
    _style_sheet(ws_x, {"A": 32, **{get_column_letter(j): 12 for j in range(2, len(months) + 3)}})

    # ---- Receipts
    _header(ws_r, 1, ["Date", "Merchant", "Category", "Net", "VAT", "Total", "Currency", "FX rate", f"Total ({cur})",
                      f"VAT ({cur})", "Payment", "Matched to bank", "File", "ID"])
    for i, rc in enumerate(d["receipts"], start=2):
        vals = [_to_date(rc["date"]), rc["merchant"], rc["category"], rc["net"], rc["vat"], rc["total"], rc["currency"],
                rc["fx_rate"], _fx_formula(f"F{i}", f"H{i}"), _fx_formula(f"E{i}", f"H{i}"), rc["payment_method"],
                "Yes" if rc["transaction_id"] else "No", rc["original_name"], rc["id"]]
        for j, v in enumerate(vals, start=1):
            ws_r.cell(row=i, column=j, value=v)
        ws_r.cell(row=i, column=1).number_format = "yyyy-mm-dd"
        for j in (4, 5, 6, 9, 10):
            ws_r.cell(row=i, column=j).number_format = MONEY
        ws_r.cell(row=i, column=8).number_format = "0.0000"
    if d["receipts"]:
        _table(ws_r, "tblReceipts", f"A1:N{len(d['receipts']) + 1}")
    ws_r.freeze_panes = "A2"
    _style_sheet(ws_r, {"A": 11, "B": 28, "C": 26, "D": 12, "E": 11, "F": 12, "G": 9, "H": 9, "I": 13, "J": 12, "K": 10,
                        "L": 15, "M": 28, "N": 6})

    # ---- Invoices
    _header(ws_i, 1, ["Direction", "Number", "Counterparty", "Issue date", "Due date", "Net", "VAT", "Total",
                      "Currency", "FX rate", f"Total ({cur})", "Status", "Days overdue", "ID"])
    for i, iv in enumerate(d["invoices"], start=2):
        vals = [iv["direction"], iv["number"], iv["counterparty"], _to_date(iv["issue_date"]),
                _to_date(iv["due_date"]) if iv["due_date"] else None, iv["net"], iv["vat"], iv["total"], iv["currency"],
                iv["fx_rate"], _fx_formula(f"H{i}", f"J{i}"), iv["status"],
                f'=IF(AND(L{i}="unpaid",ISNUMBER(E{i})),MAX(0,TODAY()-E{i}),"")', iv["id"]]
        for j, v in enumerate(vals, start=1):
            ws_i.cell(row=i, column=j, value=v)
        ws_i.cell(row=i, column=4).number_format = "yyyy-mm-dd"
        ws_i.cell(row=i, column=5).number_format = "yyyy-mm-dd"
        for j in (6, 7, 8, 11):
            ws_i.cell(row=i, column=j).number_format = MONEY
        ws_i.cell(row=i, column=10).number_format = "0.0000"
    if d["invoices"]:
        _table(ws_i, "tblInvoices", f"A1:N{len(d['invoices']) + 1}")
    ws_i.freeze_panes = "A2"
    _style_sheet(ws_i, {"A": 10, "B": 16, "C": 28, "D": 11, "E": 11, "F": 12, "G": 11, "H": 12, "I": 9, "J": 9,
                        "K": 13, "L": 9, "M": 12, "N": 6})

    # ---- VAT: line data at the bottom, summary by rate on top, everything converted to the base currency
    vat_rows = []
    for rc in d["receipts"]:
        lines = loads(rc["vat_lines"], [])
        src = [(ln.get("rate"), ln.get("net"), ln.get("vat")) for ln in lines] or (
            [(None, rc["net"], rc["vat"])] if rc["vat"] is not None else [])
        for rate_, net_, vat_ in src:
            vat_rows.append(("Receipt", "purchase", rc["date"], rc["merchant"], rate_, rc["currency"], rc["fx_rate"], net_, vat_))
    for iv in d["invoices"]:
        lines = loads(iv["vat_lines"], [])
        src = [(ln.get("rate"), ln.get("net"), ln.get("vat")) for ln in lines] or (
            [(None, iv["net"], iv["vat"])] if iv["vat"] is not None else [])
        for rate_, net_, vat_ in src:
            vat_rows.append(("Invoice " + (iv["number"] or ""), iv["direction"], iv["issue_date"], iv["counterparty"], rate_,
                             iv["currency"], iv["fx_rate"], net_, vat_))
    rates = sorted({r[4] for r in vat_rows if r[4] is not None}, reverse=True)
    _title(ws_v, f"VAT / moms overview ({cur}, estimate)", "Based on receipts and invoices in the ledger. Deduction limits (e.g. restaurant meals, cars) are NOT applied - confirm with your accountant.")
    _header(ws_v, 3, ["", "", "Input VAT (købsmoms)", "Output VAT (salgsmoms)", "Balance (to pay if positive)"])
    data_start = 9 + len(rates) + 3
    data_end = max(data_start + len(vat_rows), data_start + 1)
    DIR = f"$B${data_start + 1}:$B${data_end}"
    RATE = f"$E${data_start + 1}:$E${data_end}"
    NETB = f"$J${data_start + 1}:$J${data_end}"
    VATB = f"$K${data_start + 1}:$K${data_end}"
    ws_v["A4"] = "Total VAT"
    ws_v["A4"].font = _font(bold=True)
    ws_v["C4"] = f'=SUMIFS({VATB},{DIR},"purchase")'
    ws_v["D4"] = f'=SUMIFS({VATB},{DIR},"sales")'
    ws_v["E4"] = "=D4-C4"
    for col in "CDE":
        ws_v[f"{col}4"].number_format = MONEY
        ws_v[f"{col}4"].font = _font(bold=True)
    _header(ws_v, 7, ["Rate %", "", "Purchases net", "Purchases VAT", "Sales net", "Sales VAT"])
    for i, rate_ in enumerate(rates, start=8):
        ws_v.cell(row=i, column=1, value=rate_)
        ws_v.cell(row=i, column=3, value=f'=SUMIFS({NETB},{DIR},"purchase",{RATE},$A{i})')
        ws_v.cell(row=i, column=4, value=f'=SUMIFS({VATB},{DIR},"purchase",{RATE},$A{i})')
        ws_v.cell(row=i, column=5, value=f'=SUMIFS({NETB},{DIR},"sales",{RATE},$A{i})')
        ws_v.cell(row=i, column=6, value=f'=SUMIFS({VATB},{DIR},"sales",{RATE},$A{i})')
        for col in range(3, 7):
            ws_v.cell(row=i, column=col).number_format = MONEY
    ws_v.cell(row=data_start - 1, column=1, value="VAT lines (source data)").font = _font(bold=True, size=12, color="1F3A5F")
    _header(ws_v, data_start, ["Source", "Direction", "Date", "Counterparty", "Rate %", "Currency", "FX rate", "Net", "VAT",
                               f"Net ({cur})", f"VAT ({cur})"])
    for i, row in enumerate(vat_rows, start=data_start + 1):
        for j, v in enumerate(row, start=1):
            ws_v.cell(row=i, column=j, value=_to_date(v) if j == 3 else v)
        ws_v.cell(row=i, column=10, value=_fx_formula(f"H{i}", f"G{i}"))
        ws_v.cell(row=i, column=11, value=_fx_formula(f"I{i}", f"G{i}"))
        ws_v.cell(row=i, column=3).number_format = "yyyy-mm-dd"
        ws_v.cell(row=i, column=7).number_format = "0.0000"
        for j in (8, 9, 10, 11):
            ws_v.cell(row=i, column=j).number_format = MONEY
    _style_sheet(ws_v, {"A": 18, "B": 11, "C": 22, "D": 26, "E": 26, "F": 12, "G": 9, "H": 12, "I": 12, "J": 13, "K": 13})

    # ---- Missing documents
    _title(ws_md, "Expenses without a receipt or invoice", "Collect these documents before closing the period.")
    _header(ws_md, 4, ["Date", "Counterparty", "Description", "Category", "Amount", "Currency", f"Amount ({cur})", "ID"])
    for i, t in enumerate(d["missing"], start=5):
        for j, v in enumerate([_to_date(t["date"]), t["counterparty"], t["description"], t["category"], t["amount"],
                               t["currency"], t["amount_base"], t["id"]], start=1):
            ws_md.cell(row=i, column=j, value=v)
        ws_md.cell(row=i, column=1).number_format = "yyyy-mm-dd"
        ws_md.cell(row=i, column=5).number_format = MONEY
        ws_md.cell(row=i, column=7).number_format = MONEY
    _style_sheet(ws_md, {"A": 11, "B": 26, "C": 50, "D": 26, "E": 13, "F": 9, "G": 14, "H": 6})

    # ---- Cash book (kassebog): opening balance + movements, running balance per currency as formulas
    if d["cash"] or any(o["s"] for o in d["cash_opening"]):
        ws_k = wb.create_sheet("Cash book")
        _title(ws_k, "Cash book", "Running balance per currency. Cash receipts are booked here automatically.")
        _header(ws_k, 4, ["Date", "Text", "Category", "Currency", "In", "Out", "Balance", "Receipt"])
        r = 5
        rows = [(start, "Balance carried forward", "", o["currency"], o["s"], None) for o in d["cash_opening"] if o["s"]]
        rows += [(t["date"], t["description"] or t["counterparty"], t["category"], t["currency"], t["amount"], t["has_receipt"])
                 for t in d["cash"]]
        for date_, text, cat, cur_, amount, rec in rows:
            ws_k.cell(row=r, column=1, value=_to_date(date_)).number_format = "yyyy-mm-dd"
            ws_k.cell(row=r, column=2, value=text)
            ws_k.cell(row=r, column=3, value=cat)
            ws_k.cell(row=r, column=4, value=cur_)
            ws_k.cell(row=r, column=5, value=amount if amount > 0 else None).number_format = MONEY
            ws_k.cell(row=r, column=6, value=-amount if amount < 0 else None).number_format = MONEY
            ws_k.cell(row=r, column=7, value=f"=SUMIFS($E$5:$E{r},$D$5:$D{r},$D{r})-SUMIFS($F$5:$F{r},$D$5:$D{r},$D{r})").number_format = MONEY
            ws_k.cell(row=r, column=8, value="" if text == "Balance carried forward" else ("Yes" if rec else ("No" if amount < 0 else "")))
            r += 1
        ws_k.freeze_panes = "A5"
        _style_sheet(ws_k, {"A": 11, "B": 40, "C": 28, "D": 9, "E": 13, "F": 13, "G": 14, "H": 9})

    # python-side figures (shown in the app and used as a cross-check of the formulas)
    inc = sum(t["amount_base"] for t in tx if t["kind"] == "income" and t["amount_base"] is not None)
    exp = -sum(t["amount_base"] for t in tx if t["kind"] == "expense" and t["amount_base"] is not None)
    fname = f"{_slug(title)}_{datetime.now():%Y%m%d-%H%M%S}.xlsx"
    path = REPORT_DIR / fname
    wb.save(path)
    _register(fname, title, {"type": "overview", "start": start, "end": end})
    return {"filename": fname, "url": f"/files/reports/{fname}", "title": title, "start": start, "end": end,
            "figures": {"income": round(inc, 2), "expenses": round(exp, 2), "net": round(inc - exp, 2),
                        "transactions": len(tx), "receipts": len(d["receipts"]), "invoices": len(d["invoices"]),
                        "missing_documents": len(d["missing"]), "currency": cur,
                        "converted": sum(1 for t in tx if (t["currency"] or "").upper() != cur),
                        "without_rate": sum(1 for t in tx if t["amount_base"] is None)}}


def _register(fname, title, params):
    with db() as c:
        c.execute("INSERT INTO reports(filename, title, params, created_at) VALUES (?,?,?,?)",
                  (fname, title, json.dumps(params), now()))


# ---------------------------------------------------------------- custom workbook (assistant)

def safe_select(sql: str) -> str:
    q = sql.strip().rstrip(";").strip()
    if ";" in q:
        raise ValueError("Only one SQL statement is allowed.")
    if not re.match(r"(?is)^\s*(select|with)\b", q):
        raise ValueError("Only SELECT queries are allowed.")
    return q


# Queries written by the model (run_sql, custom workbooks, CSV export) and by MCP clients must not reach stored
# secrets: the model API key lives in settings, the mailbox app passwords in email_accounts.
PRIVATE_TABLES = {"settings", "email_accounts", "email_items", "desktop_runs"}


def _guard(action, arg1, arg2, dbname, source):
    import sqlite3
    if action == sqlite3.SQLITE_READ and arg1 in PRIVATE_TABLES:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def run_select(sql: str, limit: int = 20000) -> tuple[list[str], list[list]]:
    import sqlite3
    q = safe_select(sql)
    with db(readonly=True) as c:
        c.set_authorizer(_guard)
        try:
            cur = c.execute(q)
        except sqlite3.DatabaseError as e:
            if "prohibited" in str(e) or "not authorized" in str(e):
                raise ValueError("That table is private (settings, email and automation data cannot be queried). "
                                 "Bookkeeping tables: transactions, categories, accounts, account_cards, receipts, invoices, rules.")
            raise
        cols = [d[0] for d in cur.description]
        rows = [list(r.values()) for r in cur.fetchmany(limit)]
    return cols, rows


def build_custom(title: str, sheets: list[dict], notes: str = "") -> dict:
    wb = Workbook()
    wb.calculation.fullCalcOnLoad = True
    about = wb.active
    about.title = "About"
    _title(about, title, f"Generated {now().replace('T', ' ')} by the bookkeeping assistant")
    about["A4"] = notes or ""
    about["A4"].alignment = Alignment(wrap_text=True, vertical="top")
    about.merge_cells("A4:F8")
    _header(about, 10, ["Sheet", "Query used"])
    used = {"About"}
    summary = []
    for k, sh in enumerate(sheets):
        name = re.sub(r"[\[\]\*\?/\\:]", "", sh.get("name") or f"Sheet{k + 1}")[:31] or f"Sheet{k + 1}"
        while name in used:
            name = (name[:28] + f"_{k}")[:31]
        used.add(name)
        cols, rows = run_select(sh["sql"])
        ws = wb.create_sheet(name)
        about.cell(row=11 + k, column=1, value=name)
        about.cell(row=11 + k, column=2, value=sh["sql"]).alignment = Alignment(wrap_text=True)
        _header(ws, 1, cols)
        numeric = [all(isinstance(r[j], (int, float)) or r[j] is None for r in rows) and any(isinstance(r[j], (int, float)) for r in rows)
                   for j in range(len(cols))]
        for i, r in enumerate(rows, start=2):
            for j, v in enumerate(r, start=1):
                if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                    v = _to_date(v)
                    ws.cell(row=i, column=j).number_format = "yyyy-mm-dd"
                c = ws.cell(row=i, column=j, value=v)
                if numeric[j - 1] and isinstance(v, float):
                    c.number_format = MONEY
        last = len(rows) + 1
        if rows:
            tname = "tbl" + re.sub(r"[^A-Za-z0-9]", "", name)[:20] + str(k)
            _table(ws, tname, f"A1:{get_column_letter(len(cols))}{last}")
        if sh.get("totals") and rows:
            ws.cell(row=last + 1, column=1, value="Total").font = _font(bold=True)
            for j, isnum in enumerate(numeric, start=1):
                if isnum and j > 1:
                    L = get_column_letter(j)
                    c = ws.cell(row=last + 1, column=j, value=f"=SUBTOTAL(109,{L}2:{L}{last})")
                    c.number_format, c.font = MONEY, _font(bold=True)
        widths = {}
        for j, col in enumerate(cols, start=1):
            longest = max([len(str(col))] + [len(str(r[j - 1])) for r in rows[:200] if r[j - 1] is not None])
            widths[get_column_letter(j)] = min(max(10, longest + 2), 60)
        _style_sheet(ws, widths)
        ws.freeze_panes = "A2"
        chart = sh.get("chart") or {}
        ctype = chart.get("type", "none")
        if rows and ctype in ("bar", "column", "line", "pie"):
            try:
                xi = cols.index(chart.get("category_column") or cols[0]) + 1
                ys = [cols.index(y) + 1 for y in (chart.get("value_columns") or [c for c, isn in zip(cols, numeric) if isn][:2])]
            except ValueError as e:
                raise ValueError(f"Chart column not found in sheet '{name}': {e}")
            cats = Reference(ws, min_col=xi, min_row=2, max_row=last)
            refs = [Reference(ws, min_col=y, min_row=1, max_row=last) for y in ys]
            if ctype == "line":
                ch = LineChart()
                for ref in refs:
                    ch.add_data(ref, titles_from_data=True)
                ch.set_categories(cats)
                for s_, color in zip(ch.series, [BLUE, ORANGE, "1BAF7A", "EDA100"]):
                    s_.graphicalProperties.line.solidFill = color
                    s_.graphicalProperties.line.width = 22000
                ch.x_axis.delete = False
                ch.y_axis.delete = False
            elif ctype == "pie":
                ch = PieChart()
                ch.add_data(refs[0], titles_from_data=True)
                ch.set_categories(cats)
            else:
                ch = _bar_chart(chart.get("title") or name, cats, refs, [BLUE, ORANGE, "1BAF7A", "EDA100"],
                                horizontal=(ctype == "bar"))
            ch.title = chart.get("title") or name
            ch.height, ch.width = 9, 18
            ws.add_chart(ch, f"{get_column_letter(len(cols) + 2)}2")
        summary.append({"sheet": name, "rows": len(rows), "columns": cols})
    _style_sheet(about, {"A": 24, "B": 90})
    fname = f"{_slug(title)}_{datetime.now():%Y%m%d-%H%M%S}.xlsx"
    wb.save(REPORT_DIR / fname)
    _register(fname, title, {"type": "custom", "sheets": [s["sql"] for s in sheets]})
    return {"filename": fname, "url": f"/files/reports/{fname}", "sheets": summary}


def export_csv(sql: str, title: str) -> dict:
    cols, rows = run_select(sql, limit=200000)
    fname = f"{_slug(title)}_{datetime.now():%Y%m%d-%H%M%S}.csv"
    with open(REPORT_DIR / fname, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(rows)
    _register(fname, title, {"type": "csv", "sql": sql})
    return {"filename": fname, "url": f"/files/reports/{fname}", "rows": len(rows)}
