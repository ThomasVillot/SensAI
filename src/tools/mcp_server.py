import argparse
from dataclasses import replace

from mcp.server import MCPServer

from config import active_config, set_active_config
from .web_search import web_search_basic, web_search_deep
from .weather import weather
from .search_flights import search_flights
from .search_housing import search_housing
from .search_activities import search_activities
from .trip_memory import trip_save, trip_get, expense_save, expense_delete, place_save, budget_summary
from .trip_document import document_edit, document_get


def config_from_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", choices=["naif", "semantic"], required=True)

    args = parser.parse_args()

    config = replace(active_config(), cache_mode=args.cache)

    set_active_config(config)


config_from_args()

mcp = MCPServer("SensAI MCP")

mcp.tool()(web_search_basic)
mcp.tool()(web_search_deep)
mcp.tool()(weather)
mcp.tool()(search_flights)
mcp.tool()(search_housing)
mcp.tool()(search_activities)
mcp.tool()(trip_save)
mcp.tool()(trip_get)
mcp.tool()(expense_save)
mcp.tool()(expense_delete)
mcp.tool()(place_save)
mcp.tool()(budget_summary)
mcp.tool()(document_edit)
mcp.tool()(document_get)

if __name__ == "__main__":
    try:
        mcp.run()
    except Exception as e:
        print(f"[error] mcp server, try again")