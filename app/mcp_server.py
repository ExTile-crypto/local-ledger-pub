"""MCP server so Unsloth Studio's own chat can use the ledger tools (registered in Studio as http://<app address>:8890/mcp).

The tools are the same functions the in-app Assistant uses, with the same descriptions (tools.TOOL_DEFS). Each one runs
in a worker thread, so a slow tool (auto_categorize, a big workbook) does not freeze the web app.
"""
import asyncio

from mcp.server.mcpserver import MCPServer

from . import tools as T
from .skills import skill_body

mcp = MCPServer(
    "Bookkeeping Ledger",
    instructions=skill_body("bookkeeper-core", "chat") + "\n\nThe database layout is in the description of run_sql; "
                 "valid category names come from list_categories.",
)
D = T.tool_description


async def _call(name: str, **args) -> dict:
    return await asyncio.to_thread(T.call_tool, name, args)


@mcp.tool(description=D("ledger_overview"))
async def ledger_overview(start_date: str | None = None, end_date: str | None = None) -> dict:
    return await _call("ledger_overview", start_date=start_date, end_date=end_date)


@mcp.tool(description=D("run_sql") + "\n\n" + T.LEDGER_SCHEMA_DOC)
async def run_sql(query: str) -> dict:
    return await _call("run_sql", query=query)


@mcp.tool(description=D("search_transactions") + " Dates are YYYY-MM-DD.")
async def search_transactions(text: str | None = None, start_date: str | None = None, end_date: str | None = None,
                              category: str | None = None, account: str | None = None, min_amount: float | None = None,
                              max_amount: float | None = None, uncategorized_only: bool = False, limit: int = 50) -> dict:
    return await _call("search_transactions", text=text, start_date=start_date, end_date=end_date, category=category,
                       account=account, min_amount=min_amount, max_amount=max_amount,
                       uncategorized_only=uncategorized_only, limit=limit)


@mcp.tool(description=D("list_uncategorized"))
async def list_uncategorized(limit: int = 30) -> dict:
    return await _call("list_uncategorized", limit=limit)


@mcp.tool(description=D("set_category"))
async def set_category(category: str, transaction_ids: list[int] | None = None, counterparty: str | None = None,
                       only_uncategorized: bool = False, remember: bool = False) -> dict:
    return await _call("set_category", category=category, transaction_ids=transaction_ids, counterparty=counterparty,
                       only_uncategorized=only_uncategorized, remember=remember)


@mcp.tool(description=D("auto_categorize"))
async def auto_categorize(scope: str = "uncategorized") -> dict:
    return await _call("auto_categorize", scope=scope)


@mcp.tool(description=D("add_transactions") + " Each transaction: {date YYYY-MM-DD, amount, description, counterparty, "
                      "currency, category, notes}.")
async def add_transactions(transactions: list[dict], account_name: str | None = None) -> dict:
    return await _call("add_transactions", transactions=transactions, account_name=account_name)


@mcp.tool(description=D("save_receipt") + " payment_method: card | cash | transfer | mobile | other | unknown. "
                      "vat_lines: [{rate, net, vat, gross}]; items: [{description, quantity, total}].")
async def save_receipt(date: str, merchant: str, total: float, currency: str | None = None, net: float | None = None,
                       vat: float | None = None, category: str = "Uncategorized", payment_method: str = "unknown",
                       card_last4: str | None = None, merchant_tax_id: str | None = None,
                       vat_lines: list[dict] | None = None, items: list[dict] | None = None, notes: str = "",
                       allow_duplicate: bool = False) -> dict:
    return await _call("save_receipt", date=date, merchant=merchant, total=total, currency=currency, net=net, vat=vat,
                       category=category, payment_method=payment_method, card_last4=card_last4,
                       merchant_tax_id=merchant_tax_id, vat_lines=vat_lines, items=items, notes=notes,
                       allow_duplicate=allow_duplicate)


@mcp.tool(description=D("save_invoice") + " Dates are YYYY-MM-DD; status: unpaid | paid.")
async def save_invoice(direction: str, counterparty: str, issue_date: str, total: float, number: str = "",
                       due_date: str | None = None, net: float | None = None, vat: float | None = None,
                       currency: str | None = None, category: str = "Uncategorized", status: str = "unpaid",
                       notes: str = "") -> dict:
    return await _call("save_invoice", direction=direction, counterparty=counterparty, issue_date=issue_date,
                       total=total, number=number, due_date=due_date, net=net, vat=vat, currency=currency,
                       category=category, status=status, notes=notes)


@mcp.tool(description=D("missing_documents"))
async def missing_documents(start_date: str | None = None, end_date: str | None = None, min_amount: float = 0) -> dict:
    return await _call("missing_documents", start_date=start_date, end_date=end_date, min_amount=min_amount)


@mcp.tool(description=D("reconcile"))
async def reconcile() -> dict:
    return await _call("reconcile")


@mcp.tool(description=D("link_document"))
async def link_document(doc_type: str, doc_id: int, transaction_id: int | None = None) -> dict:
    return await _call("link_document", doc_type=doc_type, doc_id=doc_id, transaction_id=transaction_id)


@mcp.tool(description=D("convert_currency"))
async def convert_currency(amount: float, from_currency: str, to_currency: str | None = None, date: str | None = None) -> dict:
    return await _call("convert_currency", amount=amount, from_currency=from_currency, to_currency=to_currency, date=date)


@mcp.tool(description="List the valid category names with their kind (income, expense, transfer, other) and description.")
async def list_categories() -> dict:
    return await _call("list_categories")


@mcp.tool(description=D("create_overview_workbook"))
async def create_overview_workbook(start_date: str | None = None, end_date: str | None = None, title: str | None = None) -> dict:
    return await _call("create_overview_workbook", start_date=start_date, end_date=end_date, title=title)


@mcp.tool(description=D("create_custom_workbook") + " sheets = [{name, sql (one SELECT), totals (bool), chart: {type: "
                      "none|column|bar|line|pie, category_column, value_columns[], title}}].")
async def create_custom_workbook(title: str, sheets: list[dict], notes: str = "") -> dict:
    return await _call("create_custom_workbook", title=title, sheets=sheets, notes=notes)


@mcp.tool(description=D("export_csv"))
async def export_csv(query: str, title: str) -> dict:
    return await _call("export_csv", query=query, title=title)
