from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from portfolio_ops_instrument_core import instrument_store as shared_store
from portfolio_ops_instrument_core.db_models import (
    Instrument,
    InstrumentRegistryBase,
    QuoteObservation,
    QuoteObservationRevision,
    QuoteSeries,
)
from portfolio_ops_instrument_core.quote_resolver import (
    CANONICAL_QUOTE_RESOLVER_STRATEGY_VERSION,
    QUOTE_SELECTION_POLICY_VERSION,
    QuoteResolverError,
    QuoteRevisionCandidate,
    QuoteSeriesDescriptor,
    canonical_quote_selection_policy_revision,
    canonicalize_quote_selection_policy,
    resolve_explicit_quote_candidate,
    resolve_explicit_quote_in_session,
    resolve_quote_series_observation_at,
    resolve_role_quote,
    resolve_role_quote_series,
)
from portfolio_ops_instrument_core.quote_revisions import (
    make_quote_observation_id,
    make_quote_revision_id,
    make_quote_series_id,
    quote_revision_payload_hash,
)


EXACT = {
    "policy_version": "canonical_quote_freshness.v1",
    "mode": "exact_only",
    "max_age_days": 0,
}
CARRY_2 = {
    "policy_version": "canonical_quote_freshness.v1",
    "mode": "calendar_day_carry_forward",
    "max_age_days": 2,
}


def _policy(**overrides: list[str]) -> dict[str, list[str]]:
    policy = {
        "trading": ["official_nav", "close"],
        "valuation": ["official_nav", "close"],
        "total_return": ["total_return_nav"],
        "chart": ["official_nav", "close"],
        "reference": ["official_nav", "close"],
    }
    policy.update(overrides)
    return policy


@pytest.fixture
def quote_registry(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'quotes.db'}")
    InstrumentRegistryBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield engine, factory
    finally:
        engine.dispose()


def _create_fund(factory, *, policy: dict[str, list[str]] | None = None) -> str:
    record = shared_store.create_instrument(
        factory,
        instrument_name="Resolver Fund",
        instrument_type="fund",
        currency="USD",
        identifiers=[
            {
                "identifier_type": "internal",
                "identifier_value": f"resolver-{datetime.now(UTC).timestamp()}",
                "is_primary": True,
            }
        ],
        quote_selection_policy=policy or _policy(),
    )
    return str(record["instrument_id"])


def _upsert(
    factory,
    *,
    instrument_id: str,
    quote_basis: str,
    point_date: date,
    value: str,
    status: str = "complete",
) -> None:
    metric_family = "nav" if "nav" in quote_basis else "price"
    result = shared_store.upsert_market_data(
        factory,
        instrument_id=instrument_id,
        metric_family=metric_family,
        quote_basis=quote_basis,
        as_of_date=point_date,
        value=value,
        currency="USD",
        source_ref="resolver-test",
        status=status,
    )
    assert result is not None


def test_explicit_candidate_distinguishes_carry_ingestion_and_reliability() -> None:
    series = QuoteSeriesDescriptor(
        quote_series_id="series-1",
        instrument_id="fund-1",
        metric_family="nav",
        quote_basis="official_nav",
        currency="USD",
    )
    candidate = QuoteRevisionCandidate(
        quote_series_id="series-1",
        observation_id="observation-1",
        revision_id="revision-1",
        revision_number=1,
        payload_hash="sha256:payload",
        value=Decimal("100.12345678901234567890123456789"),
        status="complete",
        observation_date=date(2026, 7, 12),
        source_ref="issuer:statement",
        source_published_at=None,
        ingested_at=None,
    )

    resolution = resolve_explicit_quote_candidate(
        resolver_strategy_version=CANONICAL_QUOTE_RESOLVER_STRATEGY_VERSION,
        instrument_id="fund-1",
        metric_family="nav",
        quote_basis="official_nav",
        currency="USD",
        requested_as_of_date=date(2026, 7, 13),
        freshness_policy=CARRY_2,
        series=series,
        candidate=candidate,
    )

    assert resolution.resolution_status == "resolved"
    assert resolution.value == candidate.value
    assert resolution.freshness_status == "current"
    assert resolution.ingestion_status == "unknown"
    assert resolution.reliability_status == "qualified"
    assert resolution.reason_codes == [
        "carried_forward_observation",
        "unknown_ingestion_time",
    ]
    assert resolution.source_ref == "issuer:statement"
    assert not hasattr(resolution, "provider")


def test_role_selects_one_series_before_observation_lookup_and_window_is_one_batch(
    quote_registry,
) -> None:
    engine, factory = quote_registry
    instrument_id = _create_fund(factory)
    _upsert(
        factory,
        instrument_id=instrument_id,
        quote_basis="official_nav",
        point_date=date(2025, 1, 1),
        value="90",
    )
    _upsert(
        factory,
        instrument_id=instrument_id,
        quote_basis="close",
        point_date=date(2026, 7, 13),
        value="101",
    )

    quote = resolve_role_quote(
        factory,
        resolver_strategy_version=CANONICAL_QUOTE_RESOLVER_STRATEGY_VERSION,
        quote_selection_policy_version=QUOTE_SELECTION_POLICY_VERSION,
        instrument_id=instrument_id,
        role="valuation",
        currency="USD",
        requested_as_of_date=date(2026, 7, 13),
        freshness_policy=EXACT,
    )
    assert quote.resolution_status == "unavailable"
    assert quote.quote_basis == "official_nav"
    assert "freshness_limit_exceeded" in quote.reason_codes

    statements: list[str] = []

    def capture_statement(*args) -> None:
        statements.append(str(args[2]))

    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        window = resolve_role_quote_series(
            factory,
            resolver_strategy_version=CANONICAL_QUOTE_RESOLVER_STRATEGY_VERSION,
            quote_selection_policy_version=QUOTE_SELECTION_POLICY_VERSION,
            instrument_id=instrument_id,
            role="valuation",
            currency="USD",
            range_mode="bounded",
            start_date=date(2026, 7, 10),
            end_date=date(2026, 7, 13),
            freshness_policy=EXACT,
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)

    assert window.resolution_status == "unavailable"
    assert window.quote_basis == "official_nav"
    assert window.observations == []
    assert window.points == []
    assert "insufficient_history" in window.reason_codes
    assert len(statements) == 3
    assert sum("quote_observation" in statement for statement in statements) == 1


def test_window_exposes_all_current_states_and_applies_anchor_freshness(
    quote_registry,
) -> None:
    _, factory = quote_registry
    instrument_id = _create_fund(factory, policy=_policy(chart=["official_nav"]))
    for point_date, value, status in (
        (date(2026, 7, 9), "99", "complete"),
        (date(2026, 7, 11), "100", "complete"),
        (date(2026, 7, 12), "100.5", "partial"),
        (date(2026, 7, 13), "101", "complete"),
    ):
        _upsert(
            factory,
            instrument_id=instrument_id,
            quote_basis="official_nav",
            point_date=point_date,
            value=value,
            status=status,
        )

    window = resolve_role_quote_series(
        factory,
        resolver_strategy_version=CANONICAL_QUOTE_RESOLVER_STRATEGY_VERSION,
        quote_selection_policy_version=QUOTE_SELECTION_POLICY_VERSION,
        instrument_id=instrument_id,
        role="chart",
        currency="USD",
        range_mode="bounded",
        start_date=date(2026, 7, 10),
        end_date=date(2026, 7, 13),
        freshness_policy=CARRY_2,
    )

    assert window.resolution_status == "resolved"
    assert window.coverage_status == "partial"
    assert window.reliability_status == "qualified"
    assert window.start_anchor is not None
    assert window.start_anchor.observation_date == date(2026, 7, 9)
    assert [item.status for item in window.observations] == [
        "complete",
        "partial",
        "complete",
    ]
    assert [item.observation_date for item in window.points] == [
        date(2026, 7, 11),
        date(2026, 7, 13),
    ]
    assert window.observation_count == 3
    assert window.adopted_point_count == 2
    assert "carried_forward_observation" in window.reason_codes
    assert "partial_series" in window.reason_codes
    partial_revision = window.observations[1].revision_id
    assert partial_revision in window.calculation_dependency.revision_ids
    assert partial_revision in window.calculation_dependency.excluded_revision_ids
    assert window.start_anchor.revision_id in window.calculation_dependency.revision_ids

    blocked_day = resolve_quote_series_observation_at(
        window,
        requested_as_of_date=date(2026, 7, 12),
    )
    assert blocked_day.resolution_status == "unavailable"
    assert blocked_day.reason_codes == ["partial_series"]
    assert blocked_day.revision_id == partial_revision
    next_complete_day = resolve_quote_series_observation_at(
        window,
        requested_as_of_date=date(2026, 7, 13),
    )
    assert next_complete_day.resolution_status == "resolved"
    assert next_complete_day.value == Decimal("101")


def test_since_inception_has_no_artificial_anchor_and_changes_fingerprint(
    quote_registry,
) -> None:
    _, factory = quote_registry
    instrument_id = _create_fund(factory, policy=_policy(chart=["official_nav"]))
    for point_date, value in (
        (date(2026, 7, 1), "100"),
        (date(2026, 7, 13), "101"),
    ):
        _upsert(
            factory,
            instrument_id=instrument_id,
            quote_basis="official_nav",
            point_date=point_date,
            value=value,
        )

    since_inception = resolve_role_quote_series(
        factory,
        resolver_strategy_version=CANONICAL_QUOTE_RESOLVER_STRATEGY_VERSION,
        quote_selection_policy_version=QUOTE_SELECTION_POLICY_VERSION,
        instrument_id=instrument_id,
        role="chart",
        currency="USD",
        range_mode="since_inception",
        start_date=None,
        end_date=date(2026, 7, 13),
        freshness_policy=EXACT,
    )
    bounded = resolve_role_quote_series(
        factory,
        resolver_strategy_version=CANONICAL_QUOTE_RESOLVER_STRATEGY_VERSION,
        quote_selection_policy_version=QUOTE_SELECTION_POLICY_VERSION,
        instrument_id=instrument_id,
        role="chart",
        currency="USD",
        range_mode="bounded",
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 13),
        freshness_policy=EXACT,
    )

    assert since_inception.range_mode == "since_inception"
    assert since_inception.start_date is None
    assert since_inception.start_anchor is None
    assert "missing_anchor" not in since_inception.reason_codes
    assert since_inception.calculation_dependency.range_mode == "since_inception"
    assert (
        since_inception.calculation_dependency.fingerprint
        != bounded.calculation_dependency.fingerprint
    )


def test_in_session_resolver_reads_uncommitted_uow_state(quote_registry) -> None:
    _, factory = quote_registry
    instrument_id = "uncommitted-fund"
    series_id = make_quote_series_id(
        instrument_id=instrument_id,
        metric_family="nav",
        quote_basis="official_nav",
        currency="USD",
    )
    observation_id = make_quote_observation_id(
        quote_series_id=series_id,
        as_of_date=date(2026, 7, 13),
    )
    value = "123.45678901234567890123456789"
    payload_hash = quote_revision_payload_hash(
        value=value,
        source_ref="uow",
        status="complete",
    )
    with factory() as session:
        session.add(
            Instrument(
                instrument_id=instrument_id,
                instrument_name="Uncommitted Fund",
                instrument_type="fund",
                currency="USD",
                quote_selection_policy_json=_policy(),
                source_settings_json={},
                refresh_status_json={},
                lifecycle_state_json={"status": "active"},
            )
        )
        session.add(
            QuoteSeries(
                quote_series_id=series_id,
                instrument_id=instrument_id,
                metric_family="nav",
                quote_basis="official_nav",
                currency="USD",
            )
        )
        session.add(
            QuoteObservation(
                observation_id=observation_id,
                quote_series_id=series_id,
                as_of_date=date(2026, 7, 13),
            )
        )
        session.add(
            QuoteObservationRevision(
                revision_id=make_quote_revision_id(
                    observation_id=observation_id,
                    revision_number=1,
                ),
                observation_id=observation_id,
                revision_number=1,
                value=Decimal(value),
                source_ref="uow",
                status="complete",
                source_published_at=None,
                ingested_at=datetime.now(UTC),
                payload_hash=payload_hash,
                is_current=True,
                superseded_at=None,
            )
        )
        session.flush()

        resolution = resolve_explicit_quote_in_session(
            session,
            resolver_strategy_version=CANONICAL_QUOTE_RESOLVER_STRATEGY_VERSION,
            instrument_id=instrument_id,
            metric_family="nav",
            quote_basis="official_nav",
            currency="USD",
            requested_as_of_date=date(2026, 7, 13),
            freshness_policy=EXACT,
        )

        assert resolution.resolution_status == "resolved"
        assert resolution.value == Decimal(value)


def test_archived_alias_fails_with_stable_non_canonical_reason(quote_registry) -> None:
    _, factory = quote_registry
    canonical_id = _create_fund(factory)
    alias_id = _create_fund(factory)
    with factory.begin() as session:
        alias = session.get(Instrument, alias_id)
        assert alias is not None
        alias.lifecycle_state_json = {
            "status": "archived",
            "canonical_instrument_id": canonical_id,
        }

    resolution = resolve_role_quote(
        factory,
        resolver_strategy_version=CANONICAL_QUOTE_RESOLVER_STRATEGY_VERSION,
        quote_selection_policy_version=QUOTE_SELECTION_POLICY_VERSION,
        instrument_id=alias_id,
        role="valuation",
        currency="USD",
        requested_as_of_date=date(2026, 7, 13),
        freshness_policy=EXACT,
    )

    assert resolution.resolution_status == "unavailable"
    assert resolution.reason_codes == ["non_canonical_instrument_id"]
    assert resolution.quote_series_id is None


def test_quote_policy_requires_five_explicit_roles_and_true_total_return() -> None:
    with pytest.raises(QuoteResolverError, match="explicitly define role"):
        canonicalize_quote_selection_policy({"valuation": ["close"]})
    with pytest.raises(QuoteResolverError, match="non-total-return"):
        canonicalize_quote_selection_policy(_policy(total_return=["close"]))
    with pytest.raises(QuoteResolverError, match="Duplicate quote basis"):
        canonicalize_quote_selection_policy(
            _policy(chart=["official_nav", "official_nav"])
        )
    first = canonical_quote_selection_policy_revision(
        _policy(chart=["official_nav", "close"])
    )
    reordered = canonical_quote_selection_policy_revision(
        _policy(chart=["close", "official_nav"])
    )
    assert first != reordered


def test_index_close_never_falls_back_as_total_return(quote_registry) -> None:
    _, factory = quote_registry
    record = shared_store.create_instrument(
        factory,
        instrument_name="Price-only Index",
        instrument_type="index",
        currency="USD",
        identifiers=[
            {
                "identifier_type": "internal",
                "identifier_value": "price-only-index",
                "is_primary": True,
            }
        ],
    )
    instrument_id = str(record["instrument_id"])
    _upsert(
        factory,
        instrument_id=instrument_id,
        quote_basis="close",
        point_date=date(2026, 7, 13),
        value="100",
    )

    resolution = resolve_role_quote(
        factory,
        resolver_strategy_version=CANONICAL_QUOTE_RESOLVER_STRATEGY_VERSION,
        quote_selection_policy_version=QUOTE_SELECTION_POLICY_VERSION,
        instrument_id=instrument_id,
        role="total_return",
        currency="USD",
        requested_as_of_date=date(2026, 7, 13),
        freshness_policy=EXACT,
    )

    assert resolution.resolution_status == "unavailable"
    assert resolution.reason_codes == ["missing_quote_series"]
    assert resolution.quote_basis is None
