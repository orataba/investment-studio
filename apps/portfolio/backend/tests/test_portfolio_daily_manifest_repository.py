from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import Numeric

from portfolio_app.calculations.numeric import (
    exact_decimal_product,
    exact_decimal_subtract,
)
from portfolio_app.calculations.portfolio_daily.db_models import DEPENDENCY_TABLES
from portfolio_app.calculations.portfolio_daily.manifest_repository import (
    _DEPENDENCY_LOGICAL_NUMERIC_DOMAINS,
    _RAW_CANONICAL_NUMERIC_COLUMNS,
    DEPENDENCY_COUNT_KEYS,
    DEPENDENCY_NATURAL_KEYS,
    ManifestDependencies,
    ManifestPersistenceError,
    insert_manifest_dependencies,
)
from portfolio_ops_instrument_core.canonical_fx import effective_fx_leg_rate


pytestmark = pytest.mark.no_database


def _empty_rows() -> dict[str, list[dict[str, object]]]:
    return {table.name: [] for table in DEPENDENCY_TABLES}


def test_manifest_table_contract_covers_every_dependency_exactly_once() -> None:
    table_names = {table.name for table in DEPENDENCY_TABLES}
    assert set(DEPENDENCY_COUNT_KEYS) == table_names
    assert set(DEPENDENCY_NATURAL_KEYS) == table_names
    assert len(set(DEPENDENCY_COUNT_KEYS.values())) == len(table_names)


def test_every_unbounded_dependency_numeric_has_a_domain_or_raw_whitelist() -> None:
    unclassified: list[tuple[str, str]] = []
    for table in DEPENDENCY_TABLES:
        for column in table.c:
            if not isinstance(column.type, Numeric):
                continue
            if column.type.precision is not None or column.type.scale is not None:
                continue
            key = (table.name, column.name)
            if key not in _DEPENDENCY_LOGICAL_NUMERIC_DOMAINS and key not in (
                _RAW_CANONICAL_NUMERIC_COLUMNS
            ):
                unclassified.append(key)
    assert unclassified == []


def test_manifest_dependencies_reject_missing_or_unknown_tables() -> None:
    rows = _empty_rows()
    rows.pop(next(iter(rows)))
    with pytest.raises(ManifestPersistenceError, match="table set mismatch"):
        ManifestDependencies(rows)

    rows = _empty_rows()
    rows["not_a_dependency"] = []
    with pytest.raises(ManifestPersistenceError, match="table set mismatch"):
        ManifestDependencies(rows)


def test_dependency_counts_use_database_trigger_vocabulary() -> None:
    rows = _empty_rows()
    rows["portfolio_daily_config_input"] = [{"portfolio_id": "portfolio-a"}]
    rows["portfolio_daily_account_input"] = [{"account_id": "cash-a"}]
    dependencies = ManifestDependencies(rows)
    assert dependencies.counts == {
        "portfolio_config": 1,
        "account": 1,
        "transaction_revision": 0,
        "instrument_snapshot": 0,
        "corporate_action_window": 0,
        "corporate_action_snapshot": 0,
        "quote_window": 0,
        "quote_candidate": 0,
        "fx_path": 0,
        "fx_leg": 0,
        "prior_publication": 0,
    }


class _RecordingSession:
    def __init__(self) -> None:
        self.executions: list[object] = []

    def execute(self, statement, parameters=None):
        self.executions.append((statement, parameters))
        return None


def _insert_single_dependency(
    table_name: str,
    row: dict[str, object],
) -> _RecordingSession:
    rows = _empty_rows()
    rows[table_name] = [row]
    session = _RecordingSession()
    insert_manifest_dependencies(
        session,  # type: ignore[arg-type]
        manifest_id=uuid4(),
        run_id=uuid4(),
        dependencies=ManifestDependencies(rows),
    )
    return session


def test_excluded_raw_quote_preserves_provider_precision_without_adoption_gate() -> (
    None
):
    raw = Decimal("1." + "1234567890" * 30)
    session = _insert_single_dependency(
        "portfolio_daily_quote_candidate",
        {"decision": "excluded", "quote_value": raw},
    )
    assert len(session.executions) == 1
    inserted_rows = session.executions[0][1]
    assert inserted_rows[0]["quote_value"].as_tuple() == raw.as_tuple()


@pytest.mark.parametrize(
    ("table_name", "row"),
    (
        (
            "portfolio_daily_quote_candidate",
            {"decision": "excluded", "quote_value": 1.25},
        ),
        (
            "portfolio_daily_fx_leg",
            {"leg_resolution_status": "rejected", "quoted_rate": 1.25},
        ),
    ),
)
def test_non_adopted_raw_numeric_rejects_float_before_database_binding(
    table_name: str,
    row: dict[str, object],
) -> None:
    with pytest.raises(ManifestPersistenceError, match="must not be a float"):
        _insert_single_dependency(table_name, row)


@pytest.mark.parametrize("invalid", ("1.25", 1, True))
def test_non_adopted_raw_numeric_rejects_every_non_decimal_type(
    invalid: object,
) -> None:
    rows = (
        (
            "portfolio_daily_quote_candidate",
            {"decision": "excluded", "quote_value": invalid},
        ),
        (
            "portfolio_daily_fx_leg",
            {"leg_resolution_status": "rejected", "quoted_rate": invalid},
        ),
    )
    for table_name, row in rows:
        with pytest.raises(ManifestPersistenceError, match="must be a Decimal"):
            _insert_single_dependency(table_name, row)


@pytest.mark.parametrize(
    "invalid",
    (
        Decimal("1.0000000000001"),
        Decimal("1E+26"),
    ),
)
def test_adopted_quote_rejects_values_outside_source_price_domain(
    invalid: Decimal,
) -> None:
    with pytest.raises(ManifestPersistenceError, match="cannot be stored exactly"):
        _insert_single_dependency(
            "portfolio_daily_quote_candidate",
            {"decision": "adopted", "quote_value": invalid},
        )


def test_adopted_numeric_boundaries_are_exactly_usable_without_quantization() -> None:
    price_boundary = Decimal("9" * 26 + "." + "9" * 12)
    rate_boundary = Decimal("9" * 20 + "." + "9" * 18)
    _insert_single_dependency(
        "portfolio_daily_quote_candidate",
        {"decision": "adopted", "quote_value": price_boundary},
    )
    _insert_single_dependency(
        "portfolio_daily_corp_action_input",
        {"new_units": rate_boundary, "old_units": Decimal("1")},
    )


def test_resolved_fx_rejects_over_domain_raw_rate_but_rejected_raw_is_preserved() -> (
    None
):
    raw = Decimal("1." + "1234567890" * 30)
    with pytest.raises(ManifestPersistenceError, match="cannot be stored exactly"):
        _insert_single_dependency(
            "portfolio_daily_fx_leg",
            {
                "leg_resolution_status": "resolved",
                "quoted_rate": raw,
                "effective_rate": Decimal("1"),
                "rate_derivation_residual_exact": Decimal("0"),
            },
        )
    session = _insert_single_dependency(
        "portfolio_daily_fx_leg",
        {"leg_resolution_status": "rejected", "quoted_rate": raw},
    )
    assert len(session.executions) == 1
    inserted_rows = session.executions[0][1]
    assert inserted_rows[0]["quoted_rate"].as_tuple() == raw.as_tuple()


def test_resolved_fx_leg_effective_rate_is_a_method50_value() -> None:
    pseudo_method50 = Decimal("1.12345678901234567890123456789012345678901234567891")
    with pytest.raises(ManifestPersistenceError, match="significant method digits"):
        _insert_single_dependency(
            "portfolio_daily_fx_leg",
            {
                "leg_resolution_status": "resolved",
                "quoted_rate": Decimal("1"),
                "effective_rate": pseudo_method50,
                "rate_derivation_residual_exact": Decimal("0"),
            },
        )


def _resolved_inverse_leg_row(quoted_rate: Decimal) -> dict[str, object]:
    effective_rate = effective_fx_leg_rate(quoted_rate, inverted=True)
    return {
        "leg_resolution_status": "resolved",
        "is_inverted": True,
        "quoted_rate": quoted_rate,
        "effective_rate": effective_rate,
        "rate_derivation_residual_exact": exact_decimal_subtract(
            exact_decimal_product(effective_rate, quoted_rate),
            Decimal("1"),
        ),
        "rate_math_precision": 50,
        "rate_rounding_mode": "ROUND_HALF_EVEN",
    }


def test_manifest_boundary_replays_versioned_inverse_rate_and_residual() -> None:
    quoted_rate = Decimal("7")
    valid = _resolved_inverse_leg_row(quoted_rate)
    session = _insert_single_dependency("portfolio_daily_fx_leg", valid)
    assert len(session.executions) == 1

    forged = dict(valid)
    forged["effective_rate"] = Decimal("0.15")
    forged["rate_derivation_residual_exact"] = Decimal("0.05")
    with pytest.raises(
        ManifestPersistenceError,
        match="does not match the versioned Decimal50/HALF_EVEN",
    ):
        _insert_single_dependency("portfolio_daily_fx_leg", forged)


def test_manifest_boundary_preserves_exact_cross_rate_beyond_fifty_digits() -> None:
    inverse_rate = effective_fx_leg_rate(
        Decimal("7.000000000000000001"),
        inverted=True,
    )
    direct_rate = Decimal("1.234567890123456789")
    resolved_rate = exact_decimal_product(inverse_rate, direct_rate)
    assert len(resolved_rate.as_tuple().digits) > 50

    session = _insert_single_dependency(
        "portfolio_daily_fx_path",
        {
            "resolution_status": "resolved",
            "resolved_rate": resolved_rate,
            "rate_derivation_residual_exact": Decimal("0"),
        },
    )
    inserted_rows = session.executions[0][1]
    assert isinstance(inserted_rows, list)
    assert inserted_rows[0]["resolved_rate"] == resolved_rate
