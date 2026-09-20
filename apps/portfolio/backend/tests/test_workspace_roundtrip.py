"""Risk consumers must accept the same financial data after a process restart."""
from collections import OrderedDict

from portfolio_app.services import holdings_workspace, source_cache, workspace_cache, workspace_precompute


PORTFOLIO_ID = "investment-studio"


def test_persisted_analysis_preserves_tail_and_risk_context_http_results(client, monkeypatch):
    # Publish accounting facts first; the page projection remains absent while
    # both baseline responses are built from the canonical Python structures.
    assert client.get(f"/api/portfolios/{PORTFOLIO_ID}/performance").status_code == 200
    paths = ("tail-risk", "risk-context")
    expected = {}
    for path in paths:
        response = client.get(f"/api/portfolios/{PORTFOLIO_ID}/{path}")
        assert response.status_code == 200, response.text
        expected[path] = response.json()
    assert expected["risk-context"]["holdings"]
    assert expected["risk-context"]["tail_risk"] == expected["tail-risk"]
    assert expected["tail-risk"]["status"] == "available"
    assert expected["tail-risk"]["var_amount"] is not None
    assert expected["tail-risk"]["first_scenario_start_date"]
    assert workspace_precompute.precompute_portfolio_workspace(PORTFOLIO_ID)
    _portfolio, current_date, _keys = workspace_precompute._current_projection_inputs(PORTFOLIO_ID)

    original_builder = holdings_workspace._build_holdings_analytics_workspace

    def historical_only(*args, **kwargs):
        # Risk context deliberately compares with the prior accounting day.
        # Historical requests remain on demand; today's analysis must come
        # from the persisted JSON, even though the memory cache is empty.
        assert kwargs["resolved_as_of_date"] < current_date
        return original_builder(*args, **kwargs)

    published_reads = []
    original_read = workspace_cache.read_workspace_projection

    def record_published_read(portfolio_id, surface, key):
        payload = original_read(portfolio_id, surface, key)
        if payload is not None:
            published_reads.append((portfolio_id, surface))
        return payload

    monkeypatch.setattr(holdings_workspace, "_build_holdings_analytics_workspace", historical_only)
    monkeypatch.setattr(workspace_cache, "read_workspace_projection", record_published_read)
    for path in paths:
        monkeypatch.setattr(source_cache, "_cache", OrderedDict())
        monkeypatch.setattr(source_cache, "_cache_total_size_bytes", 0)
        published_reads.clear()
        response = client.get(f"/api/portfolios/{PORTFOLIO_ID}/{path}")
        assert response.status_code == 200, response.text
        assert (PORTFOLIO_ID, "holdings_analytics") in published_reads
        assert response.json() == expected[path]
