from copy import deepcopy
from datetime import date
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from watchlist_app.services.canonical_recalc import CanonicalRecalcService, _compute_downside_deviation, _compute_sortino


def comparison(*, peer_patch=None):
    end = date(2026, 9, 30)
    names = ["target", "peer1", "peer2", "peer3", "peer4"]
    node = SimpleNamespace(node_id="fund-private-quant", is_leaf=True, instrument_type="private_fund",
                           path_node_ids_json=["fund-private-quant"], path_labels_json=["量化"])
    performance = {name: SimpleNamespace(as_of_date=end, return_1m=5.0, annualized_return=5.0,
                   max_drawdown=-40.0 if name == "target" else 0.0) for name in names}
    risks = {name: SimpleNamespace(as_of_date=end, volatility=10, sharpe_ratio=1, sortino_ratio=2) for name in names}
    basis = {"currency": "CNY", "return_kind": "total_return", "frequency": "daily",
             "windows": {"return_1m": {"anchor_date": "2026-08-31", "end_date": "2026-09-30", "observation_status": "complete", "reason": None}}}
    bases = {name: deepcopy(basis) for name in names}
    if peer_patch:
        for name in names[1:]:
            bases[name].update(peer_patch)
    context = {"node_by_id": {node.node_id: node}, "assigned_node_by_asset": dict.fromkeys(names, node),
               "attributes_by_asset": {name: {"primary_geographic_exposure": "中国"} for name in names},
               "active_peer_instrument_ids": set(names), "performance_by_asset": performance,
               "risk_by_asset": risks, "comparison_basis_by_asset": bases}
    return CanonicalRecalcService()._peer_comparison_payload(None, instrument_id="target", taxonomy_node=node,
        performance_snapshot=performance["target"], context=context)


def test_peer_rank_only_uses_comparable_fixed_windows_and_not_inception_risk():
    result = comparison()
    assert result["status"] == "ready"
    assert result["sample_count"] == 4
    assert [row["metric_key"] for row in result["metrics"]] == ["return_1m"]
    row = result["metrics"][0]
    assert row["peer_median"] == 5
    assert row["comparison_window"] == {"anchor_date": "2026-08-31", "end_date": "2026-09-30", "observation_status": "complete", "reason": None}


@pytest.mark.parametrize("patch", [
    {"currency": "USD"}, {"currency": None}, {"return_kind": "price_return"}, {"frequency": "weekly"},
    {"windows": {}},
    {"windows": {"return_1m": {"anchor_date": "2026-09-01", "end_date": "2026-09-30"}}},
    {"windows": {"return_1m": {"anchor_date": "2026-08-31", "end_date": "2026-09-29"}}},
])
def test_same_snapshot_date_cannot_make_different_peer_bases_comparable(patch):
    result = comparison(peer_patch=patch)
    assert result["metrics"] == []
    assert result["status"] == "insufficient_data"
    assert result["sample_count"] == 0


def test_peer_exclusions_disclose_interior_gaps_even_when_no_metric_can_be_published():
    result = comparison(peer_patch={"windows": {"return_1m": {"anchor_date": "2026-08-31", "end_date": "2026-09-30",
        "observation_status": "unavailable", "reason": "计算区间存在缺失观察值"}}})
    assert result["metrics"] == []
    assert result["candidate_count"] == 4
    assert len(result["excluded_windows"]) == 4
    assert all(item["reason"] == "计算区间存在缺失观察值" for item in result["excluded_windows"])


def test_observed_positive_returns_have_zero_downside_but_no_sortino_ratio():
    points = [{"as_of_date": date(2026, 9, 28 + index), "value": value}
              for index, value in enumerate([100, 101, 102])]
    assert _compute_downside_deviation(points) == 0
    assert _compute_sortino(points) is None
    assert _compute_downside_deviation(points[:2]) is None


def test_peer_windows_require_complete_interior_intervals_without_blocking_later_windows():
    from investment_studio_instrument_core import build_return_intervals
    from watchlist_app.services.calculation_frequency import _market_calendar_sessions, build_calculation_frequency_context
    from watchlist_app.services.canonical_recalc import _peer_comparison_basis, _comparable_peer_window

    days = _market_calendar_sessions("XNYS", date(2026, 6, 30), date(2026, 9, 30))

    def basis(missing):
        points = [{"as_of_date": day, "value": 100 + index} for index, day in enumerate(days) if day != missing]
        context = build_calculation_frequency_context(points, expected_frequency="daily", market_calendar="XNYS")
        intervals = build_return_intervals(points, date_key="as_of_date", break_dates=context["missing_observation_dates"])
        return _peer_comparison_basis(snapshot=None, nav_points=points, selection={"return_kind": "total_return"},
            frequency=context["profile"], currency="USD", return_series={"intervals": intervals})

    complete = basis(None)
    old_gap = basis(date(2026, 7, 8))
    in_window_gap = basis(date(2026, 9, 8))
    assert _comparable_peer_window(complete, old_gap, "return_1m")
    assert not _comparable_peer_window(complete, old_gap, "return_3m")
    assert not _comparable_peer_window(complete, in_window_gap, "return_1m")
    assert "缺失观察值" in in_window_gap["windows"]["return_1m"]["reason"]
    assert in_window_gap["windows"]["return_1m"]["anchor_date"] == complete["windows"]["return_1m"]["anchor_date"]


def test_crypto_navigation_category_does_not_require_an_impossible_peer_geography():
    node = SimpleNamespace(node_id="crypto-native", is_leaf=True, instrument_type="crypto",
                           path_node_ids_json=["crypto-native"], path_labels_json=["原生加密资产"])
    result = CanonicalRecalcService()._peer_comparison_payload(None, instrument_id="btc", taxonomy_node=node, performance_snapshot=None)
    assert result["status"] == "taxonomy_not_comparable"
    assert result["peer_dimensions"] == {}


@pytest.mark.parametrize("state", ["pending_recalc", "stale"])
def test_pending_or_failed_configuration_cannot_publish_an_old_peer_basis(monkeypatch, state):
    from watchlist_app.services import canonical_recalc, read_model_freshness

    service = CanonicalRecalcService()
    node = SimpleNamespace(node_id="fund-private-quant")
    monkeypatch.setattr(canonical_recalc, "_active_peer_instrument_ids", lambda session: {"peer"})
    monkeypatch.setattr(service.taxonomy_repository, "list_nodes", lambda *args, **kwargs: [node])
    monkeypatch.setattr(service.taxonomy_repository, "list_assignments", lambda *args, **kwargs: [SimpleNamespace(instrument_id="peer", node_id=node.node_id)])
    monkeypatch.setattr(service.attribute_repository, "get_values_for_assets", lambda *args: [])
    snapshot = SimpleNamespace(instrument_id="peer", snapshot_id="saved", methodology_version=canonical_recalc.PERFORMANCE_METHODOLOGY_VERSION)
    monkeypatch.setattr(service.snapshot_repository, "list_current_performance", lambda *args, **kwargs: [snapshot])
    basis = {"snapshot_id": "saved", "comparison_policy_version": canonical_recalc.PEER_COMPARISON_POLICY_VERSION}
    session = SimpleNamespace(execute=lambda statement: [("peer", basis)])
    monkeypatch.setattr(read_model_freshness, "recalculation_freshness_overrides", lambda *args, **kwargs: {})
    assert service._peer_comparison_context(session)["comparison_basis_by_asset"] == {"peer": basis}
    monkeypatch.setattr(read_model_freshness, "recalculation_freshness_overrides", lambda *args, **kwargs: {"peer": {"data_freshness_status": state}})
    assert service._peer_comparison_context(session)["comparison_basis_by_asset"] == {}


@pytest.mark.parametrize("missing", [date(2026, 7, 8), date(2026, 9, 8)])
def test_canonical_and_peer_windows_share_the_same_missing_observation_contract(monkeypatch, missing):
    from investment_studio_instrument_core import build_return_intervals
    from watchlist_app.services.calculation_frequency import _market_calendar_sessions, build_calculation_frequency_context
    service = CanonicalRecalcService()
    days = _market_calendar_sessions("XNYS", date(2026, 6, 30), date(2026, 9, 30))
    points = [{"as_of_date": day, "value": 100 + index} for index, day in enumerate(days) if day != missing]
    context = build_calculation_frequency_context(points, expected_frequency="daily", market_calendar="XNYS")
    series = {"frequency": context["profile"], "intervals": build_return_intervals(points, date_key="as_of_date", break_dates=context["missing_observation_dates"])}
    def retain(_session, **kwargs):
        return SimpleNamespace(snapshot_id=kwargs["snapshot_id"], **kwargs["data"])
    monkeypatch.setattr(service.snapshot_repository, "replace_performance", retain)
    monkeypatch.setattr(service.snapshot_repository, "replace_risk", retain)
    now = datetime(2026, 10, 1, tzinfo=UTC)
    snapshot = service._replace_performance_snapshot(None, instrument_id="test", nav_points=points,
        calculation_frequency_profile=context["profile"], return_series=series, source_cutoff_at=now, now=now)
    risk = service._replace_risk_snapshot(None, instrument_id="test", nav_points=points,
        calculation_frequency_profile=context["profile"], return_series=series, source_cutoff_at=now, now=now)
    payload = service._performance_payload(performance_snapshot=snapshot, peer_comparison=None, nav_points=points,
        calculation_frequency_profile=context["profile"], return_series=series)
    windows = {row["window"]: row for row in payload["trailing_returns"]}
    assert snapshot.return_3m is None
    assert snapshot.return_1w is not None
    assert risk.volatility is None and snapshot.max_drawdown is None
    assert payload["growth_chart_series"] == []
    if missing.month == 7:
        assert snapshot.return_1m is not None
        assert windows["1M"]["observation_status"] == "complete"
    else:
        assert snapshot.return_1m is None
        assert windows["1M"]["observation_status"] == "unavailable"
        assert "缺失观察值" in windows["1M"]["reason"]


@pytest.mark.parametrize('withdrawn', [False, True])
def test_exposure_refresh_publishes_performance_and_chart_from_one_current_sample(client, monkeypatch, withdrawn):
    from .conftest import TEST_SHARED_INSTRUMENTS, seed_shared_instrument
    from watchlist_app.services import canonical_recalc

    instrument = deepcopy(TEST_SHARED_INSTRUMENTS['sxv264'])
    instrument['source_settings'] = {'source_mode': 'manual', 'expected_frequency': 'event_driven'}
    seed_shared_instrument(instrument)
    assert client.post('/api/instruments/sxv264/resolve').status_code == 200
    assert client.post('/api/recalc/instruments/sxv264/execute', json={'job_type': 'all', 'trigger_type': 'test'}).status_code == 200
    before = client.get('/api/instruments/sxv264/performance').json()
    if withdrawn:
        current = deepcopy(canonical_recalc.get_shared_instrument('sxv264'))
        current['market_data'] = []
        monkeypatch.setattr(canonical_recalc, 'get_shared_instrument', lambda instrument_id: current)
    else:
        latest = deepcopy(instrument['market_data'][-1])
        latest.update(as_of_date='2026-04-15', value='90.0')
        instrument['market_data'].append(latest)
        seed_shared_instrument(instrument)
    result = client.post('/api/recalc/instruments/sxv264/execute', json={'job_type': 'exposure', 'trigger_type': 'test'})
    assert result.status_code == 200, result.text
    performance = client.get('/api/instruments/sxv264/performance').json()
    risk = client.get('/api/instruments/sxv264/risk').json()
    chart = client.get('/api/instruments/sxv264/chart').json()
    if withdrawn:
        assert performance['snapshot_metadata'] is None
        assert risk['snapshot_metadata'] is None
        assert chart['return_series']['wealth_points'] == []
    else:
        assert performance['comparison_basis']['snapshot_id'] != before['comparison_basis']['snapshot_id']
        assert performance['snapshot_metadata']['as_of_date'] == '2026-04-15'
        assert risk['snapshot_metadata']['as_of_date'] == '2026-04-15'
        assert chart['return_series']['wealth_points'][-1]['date'] == '2026-04-15'
        weekly = next(row for row in performance['trailing_returns'] if row['window'] == '1W')
        assert weekly['end_date'] == '2026-04-15'
        assert weekly['investment_nav'] == pytest.approx(-10)
        assert performance['comparison_basis']['windows']['return_1w']['end_date'] == weekly['end_date']
