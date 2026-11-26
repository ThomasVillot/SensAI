from importlib import import_module

from .stubs import make_stub

TOOL_NAMES = [
    "weather",
    "search_activities",
    "search_flights",
    "search_housing",
    "web_search_basic",
    "web_search_deep",
    "trip_save",
    "trip_get",
    "expense_save",
    "expense_delete",
    "place_save",
    "budget_summary",
    "document_edit",
    "document_get",
]


def _load_tool(name: str):
    try:
        return import_module(f".{name}", package=__package__)
    except ModuleNotFoundError as exc:
        if exc.name == f"{__package__}.{name}":
            return make_stub(name, f"module src/tools/{name}.py absent")
        return make_stub(name, f"import impossible (module manquant : {exc.name})")
    except Exception as exc:
        return make_stub(name, f"import impossible ({type(exc).__name__}: {exc})")


tool_handler = {name: _load_tool(name) for name in TOOL_NAMES}
