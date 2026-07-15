from __future__ import annotations

from portfolio_app.services import performance


def test_equity_without_detected_corporate_action_has_no_generic_warning(monkeypatch) -> None:
    monkeypatch.setattr(
        performance,
        "list_registry_corporate_actions",
        lambda _instrument_ids: [
            {
                "instrument_id": "equity-1",
                "effective_date": "2026-06-01",
                "status": "confirmed",
            }
        ],
    )

    assert performance.corporate_action_quality_warnings(
        {"equity"},
        {"equity-1"},
    ) == []


def test_detected_corporate_action_warning_identifies_member_date_and_remedy(monkeypatch) -> None:
    monkeypatch.setattr(
        performance,
        "list_registry_corporate_actions",
        lambda _instrument_ids: [
            {
                "instrument_id": "equity-1",
                "effective_date": "2026-07-01",
                "status": "detected",
            }
        ],
    )

    warnings = performance.corporate_action_quality_warnings(
        {"equity"},
        {"equity-1"},
    )

    assert len(warnings) == 1
    assert "Corporate action review required" in warnings[0]
    assert "equity-1 effective 2026-07-01" in warnings[0]
    assert "Confirm the issuer, exchange, or depository ratio" in warnings[0]


def test_non_equity_instruments_do_not_query_corporate_actions(monkeypatch) -> None:
    def unexpected_query(_instrument_ids):
        raise AssertionError("Corporate actions must not be queried for ineligible types.")

    monkeypatch.setattr(
        performance,
        "list_registry_corporate_actions",
        unexpected_query,
    )

    assert performance.corporate_action_quality_warnings({"bond"}, {"bond-1"}) == []
