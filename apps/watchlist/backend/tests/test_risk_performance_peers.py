from datetime import UTC, datetime

import pytest

from .conftest import canonical_quote_policy, seed_shared_instrument
from watchlist_app.db.models import InstrumentChartReadModel, InstrumentDetail, InstrumentManualProfile
from watchlist_app.db.session import get_session_factory
from watchlist_app.services.risk_performance import performance_evidence


LEAF = "fund-private-equity-quant-index-enhanced"
OTHER_LEAF = "fund-private-equity-quant-stock-selection"
TARGET = "peer-target"


def series(points):
    return {"research_returns": {"currency": "CNY", "frequency": {"resolved_frequency": "weekly"},
        "metadata": {"return_kind": "total_return", "return_series_status": "complete", "quote_basis": "total_return_nav"},
        "points": [{"date": day, "value": value} for day, value in points]}}


def set_classification(client, iid, node):
    response = client.put(f"/api/taxonomies/instrument-taxonomy/instruments/{iid}",
        json={"node_id": node, "updated_by": "test"})
    assert response.status_code == 200, response.text


def seed(client):
    for iid in (TARGET, "peer-same", "peer-region", "peer-leaf"):
        seed_shared_instrument({"instrument_id": iid, "instrument_name": iid, "instrument_type": "private_fund",
            "currency": "CNY", "quote_selection_policy": canonical_quote_policy("private_fund"), "market_data": [],
            "identifiers": [{"identifier_type": "ticker", "identifier_value": iid.upper(), "is_primary": True}]})
        with get_session_factory()() as session:
            session.add(InstrumentDetail(instrument_id=iid, instrument_type="private_fund", detail_view_type="private_fund", instrument_name=iid))
            session.commit()
        set_classification(client, iid, OTHER_LEAF if iid == "peer-leaf" else LEAF)
        response = client.post(f"/api/instrument-attributes/instruments/{iid}", json={"values": [
            {"attribute_key": "primary_geographic_exposure", "value": "美国" if iid == "peer-region" else "中国 A 股"}]})
        assert response.status_code == 200, response.text
    seed_charts()


def seed_charts():
    # Classification updates rebuild read models; supply retained series after those API writes.
    with get_session_factory()() as session:
        for iid in (TARGET, "peer-same", "peer-region", "peer-leaf"):
            points = [("2026-06-26", 1), ("2026-07-31", 1.01), ("2026-08-28", 1.04), ("2026-09-04", 1.05)]
            if iid == TARGET:
                points = [("2026-06-19", 0.9), ("2026-06-26", 1), ("2026-07-31", 0.98), ("2026-08-28", 0.95), ("2026-09-04", 0.96)]
            session.merge(InstrumentChartReadModel(instrument_id=iid, data_freshness_status="current", payload_json=series(points)))
        session.commit()


def test_saved_fund_page_peer_scope_uses_common_series_without_ranking_snapshots(client):
    seed(client)
    page = client.get(f"/api/instruments/{TARGET}/performance")
    assert page.status_code == 200, page.text
    current = page.json()["peer_comparison"]
    with get_session_factory()() as session:
        evidence = performance_evidence(session, TARGET)
    group = evidence["peer_group"]
    assert group["peer_node_id"] == current["peer_node_id"] == LEAF
    assert group["peer_dimensions"] == current["peer_dimensions"] == {"primary_geographic_exposure": "中国 A 股"}
    assert group["scope_status"] == "ready"
    assert "ui_snapshot_status" not in group
    assert group["instrument_ids"] == ["peer-same"]
    assert len(evidence["comparisons"]) == 1
    comparison = evidence["comparisons"][0]
    assert comparison["role"] == "taxonomy_peer" and comparison["configuration_source"] == "instrument_taxonomy"
    assert comparison["comparison"]["sample_start"] == "2026-06-26"
    assert comparison["comparison"]["sample_end"] == "2026-09-04"
    assert comparison["comparison"]["observations"] == 4
    target = next(row for row in comparison["comparison"]["rows"] if row["instrument_id"] == TARGET)
    assert target["excess_return_pp"] == pytest.approx(-9)
    assert len(comparison["monthly_periods"]) == 3
    assert all(row["excess_return_pp"] < 0 for row in comparison["monthly_periods"][:2])
    assert comparison["monthly_periods"][-1]["latest_month_to_date"]
    assert "metrics" not in group


def test_taxonomy_peer_requires_same_currency_frequency_and_common_dates(client):
    seed(client)
    with get_session_factory()() as session:
        chart = session.get(InstrumentChartReadModel, "peer-same")
        original = chart.payload_json["research_returns"]
        for patch, expected in (({"currency": "USD"}, "币种不同"),
            ({"frequency": {"resolved_frequency": "daily"}}, "频率不同"),
            ({"points": [{"date": "2026-06-26", "value": 1}]}, "共同实际观察日不足")):
            chart.payload_json = {"research_returns": {**original, **patch}}
            session.commit()
            evidence = performance_evidence(session, TARGET)
            assert evidence["peer_group"]["instrument_ids"] == ["peer-same"]
            assert not evidence["comparisons"]
            assert any(expected in note for note in evidence["limitations"])


def test_current_classification_changes_peers_and_explicit_comparisons_stay_distinct(client):
    seed(client)
    set_classification(client, TARGET, OTHER_LEAF)
    seed_charts()
    with get_session_factory()() as session:
        evidence = performance_evidence(session, TARGET)
        assert evidence["peer_group"]["instrument_ids"] == ["peer-leaf"]
        assert [row["instrument_id"] for row in evidence["comparisons"]] == ["peer-leaf"]
        session.add(InstrumentManualProfile(instrument_id=TARGET, updated_at=datetime.now(UTC),
            nav_settings_json={"default_benchmark_instrument_id": "peer-same", "peer_baseline_instrument_ids": ["peer-region"]}))
        session.commit()
    set_classification(client, TARGET, None)
    seed_charts()
    with get_session_factory()() as session:
        evidence = performance_evidence(session, TARGET)
    assert evidence["peer_group"]["scope_status"] == "missing_taxonomy"
    assert evidence["peer_group"]["instrument_ids"] == []
    assert [(row["instrument_id"], row["role"]) for row in evidence["comparisons"]] == [
        ("peer-same", "configured_benchmark"), ("peer-region", "configured_peer")]
    assert all(row["configuration_source"] == "instrument.nav_settings" for row in evidence["comparisons"])
    assert any("未保存同类分类" in note for note in evidence["limitations"])
