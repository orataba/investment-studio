from copy import deepcopy
from datetime import date
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
             "windows": {"return_1m": {"anchor_date": "2026-08-31", "end_date": "2026-09-30"}}}
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
    assert row["comparison_window"] == {"anchor_date": "2026-08-31", "end_date": "2026-09-30"}


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


def test_observed_positive_returns_have_zero_downside_but_no_sortino_ratio():
    points = [{"as_of_date": date(2026, 9, 28 + index), "value": value}
              for index, value in enumerate([100, 101, 102])]
    assert _compute_downside_deviation(points) == 0
    assert _compute_sortino(points) is None
    assert _compute_downside_deviation(points[:2]) is None
