"""Transport fixture for MCP tests; API endpoint tests cover actual SQL projection."""
from watchlist_app.services import research_tool_projection as projection
from watchlist_app.services.research_estimate_tools import estimate_company


def server_projection(backend):
    def request(suffix, payload=None):
        if suffix == "read":
            selector = dict(payload)
            resource = selector.pop("resource")
            if resource == "estimates":
                context = backend("context")
                estimate = next((row for row in context.get("sector_estimate_evidence", []) if row["instrument_id"] == selector["instrument_id"]), None)
                if estimate is None:
                    estimate = next((row.get("analyst_estimate_history") for row in context.get("instrument_inputs", []) if row["instrument_id"] == selector["instrument_id"]), None)
                return {"analyst_estimate_history": estimate_company(estimate, selector["symbol"])} if estimate else {}
            return getattr(projection, "read_research_" + resource)(backend, **selector)
        return backend(suffix, payload) if payload is not None else backend(suffix)
    return request
