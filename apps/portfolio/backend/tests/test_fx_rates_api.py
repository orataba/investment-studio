from portfolio_app.api.routes import fx_rates


def test_portfolio_fx_rates_loads_shared_rates(client, monkeypatch):
    monkeypatch.setattr(fx_rates, "load_shared_fx_rates", lambda: {
        "supported_currencies": ["USD", "CNY"],
        "maintained_pairs": ["USD/CNY"],
        "rates": [],
    })

    response = client.get("/api/portfolios/investment-studio/fx-rates")

    assert response.status_code == 200
    assert response.json() == {
        "portfolio_id": "investment-studio",
        "supported_currencies": ["USD", "CNY"],
        "maintained_pairs": ["USD/CNY"],
        "rates": [],
    }
