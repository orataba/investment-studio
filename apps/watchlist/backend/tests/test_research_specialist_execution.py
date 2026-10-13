"""One main harness owns publication; native children receive role tool filters."""
import asyncio

from watchlist_app import research_mcp


def test_host_registers_safe_union_for_dynamic_children_and_one_final_submit(monkeypatch):
    monkeypatch.setenv("INVESTMENT_STUDIO_WATCHLIST_HARNESS_MODE", "sector")
    monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_SECTION", "overview")
    tools = {tool.name for tool in asyncio.run(research_mcp.mcp.list_tools())}
    assert {"search_market_information", "search_public_information", "run_quant_analysis",
        "compare_instruments", "read_research_section", "submit_research_review",
        "list_wind_tools", "query_wind_data"} <= tools
    assert not tools & {"submit_research_section", "record_investment_view", "manage_research_theme",
        "read_portfolio_holdings", "calculate_portfolio_allocation"}
