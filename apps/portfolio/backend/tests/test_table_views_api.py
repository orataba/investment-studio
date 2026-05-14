from __future__ import annotations


def test_table_view_store_round_trip_persists_by_portfolio_and_scope(client) -> None:
    empty_response = client.get("/api/portfolios/yungu/table-views/holdings")
    assert empty_response.status_code == 200
    assert empty_response.json()["store"] is None

    store = {
        "activeViewId": "custom:test",
        "views": [
            {
                "id": "custom:test",
                "name": "My View",
                "description": None,
                "state": {"columns": ["instrument"], "groupBy": "none"},
            }
        ],
    }
    put_response = client.put("/api/portfolios/yungu/table-views/holdings", json={"store": store})
    assert put_response.status_code == 200
    payload = put_response.json()
    assert payload["portfolio_id"] == "yungu"
    assert payload["view_scope"] == "holdings"
    assert payload["store"] == store
    assert payload["created_at"]
    assert payload["updated_at"]

    get_response = client.get("/api/portfolios/yungu/table-views/holdings")
    assert get_response.status_code == 200
    assert get_response.json()["store"] == store

    performance_store = {
        "activeViewId": "system:risk-attribution",
        "views": [],
    }
    performance_response = client.put(
        "/api/portfolios/yungu/table-views/performance_calculation",
        json={"store": performance_store},
    )
    assert performance_response.status_code == 200
    assert performance_response.json()["store"] == performance_store

    holdings_response = client.get("/api/portfolios/yungu/table-views/holdings")
    assert holdings_response.status_code == 200
    assert holdings_response.json()["store"] == store

    updated_store = {**store, "activeViewId": "system:default"}
    update_response = client.put("/api/portfolios/yungu/table-views/holdings", json={"store": updated_store})
    assert update_response.status_code == 200
    assert update_response.json()["store"] == updated_store


def test_table_view_store_rejects_unknown_scope_and_missing_portfolio(client) -> None:
    bad_scope = client.get("/api/portfolios/yungu/table-views/not-a-scope")
    assert bad_scope.status_code == 422

    missing_portfolio_get = client.get("/api/portfolios/missing/table-views/holdings")
    assert missing_portfolio_get.status_code == 404

    missing_portfolio = client.put(
        "/api/portfolios/missing/table-views/holdings",
        json={"store": {"activeViewId": "system:default", "views": []}},
    )
    assert missing_portfolio.status_code == 404
