from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
import json

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from portfolio_ops_instrument_core import instrument_store as shared_store
from portfolio_ops_instrument_core import canonical_fx as canonical_fx_module
from portfolio_ops_instrument_core.canonical_fx import (
    CANONICAL_FX_RESOLVER_STRATEGY_VERSION,
    CanonicalFxCalculationDependency,
    CanonicalFxResolution,
    CanonicalFxResolverError,
    resolve_canonical_fx_window_book_in_session,
)
from portfolio_ops_instrument_core.db_models import (
    InstrumentRegistryBase,
    QuoteObservation,
    QuoteObservationRevision,
    QuoteSeries,
)
from portfolio_ops_instrument_core.models import QuoteFreshnessPolicy
from portfolio_ops_instrument_core.quote_revisions import (
    make_quote_revision_id,
    quote_revision_payload_hash,
)


CONSUMER_POLICY_VERSION = "canonical-fx-test-consumer.v1"
CARRY_5 = QuoteFreshnessPolicy(
    policy_version="canonical_quote_freshness.v1",
    mode="calendar_day_carry_forward",
    max_age_days=5,
)


@pytest.fixture
def fx_registry(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'canonical-fx.db'}")
    InstrumentRegistryBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield engine, factory
    finally:
        engine.dispose()


def _create_fx_instruments(factory) -> None:
    for base_currency, quote_currency in (("USD", "HKD"), ("USD", "CNY")):
        instrument_id = f"fx-{base_currency.lower()}-{quote_currency.lower()}"
        record = shared_store.create_instrument(
            factory,
            instrument_name=f"{base_currency}/{quote_currency} Spot",
            instrument_type="fx",
            currency=quote_currency,
            identifiers=[
                {
                    "identifier_type": "internal",
                    "identifier_value": instrument_id,
                    "is_primary": True,
                }
            ],
        )
        assert record["instrument_id"] == instrument_id


def _upsert_fx(
    factory,
    *,
    quote_currency: str,
    point_date: date,
    value: str,
    status: str = "complete",
    source_ref: str | None = None,
) -> None:
    instrument_id = f"fx-usd-{quote_currency.lower()}"
    result = shared_store.upsert_market_data(
        factory,
        instrument_id=instrument_id,
        metric_family="fx",
        quote_basis="spot",
        as_of_date=point_date,
        value=value,
        currency=quote_currency,
        source_ref=source_ref or f"test:{instrument_id}",
        status=status,
    )
    assert result is not None


def _withdraw_current_fx_observation(
    factory,
    *,
    quote_currency: str,
    point_date: date,
) -> None:
    instrument_id = f"fx-usd-{quote_currency.lower()}"
    now = datetime.now(UTC)
    with factory() as session:
        observation = session.scalar(
            select(QuoteObservation)
            .join(
                QuoteSeries,
                QuoteSeries.quote_series_id == QuoteObservation.quote_series_id,
            )
            .where(
                QuoteSeries.instrument_id == instrument_id,
                QuoteSeries.metric_family == "fx",
                QuoteSeries.quote_basis == "spot",
                QuoteSeries.currency == quote_currency,
                QuoteObservation.as_of_date == point_date,
            )
        )
        assert observation is not None
        current = session.scalar(
            select(QuoteObservationRevision).where(
                QuoteObservationRevision.observation_id == observation.observation_id,
                QuoteObservationRevision.is_current.is_(True),
            )
        )
        assert current is not None
        current.is_current = False
        current.superseded_at = now
        revision_number = current.revision_number + 1
        session.flush()
        session.add(
            QuoteObservationRevision(
                revision_id=make_quote_revision_id(
                    observation_id=str(observation.observation_id),
                    revision_number=revision_number,
                ),
                observation_id=observation.observation_id,
                revision_number=revision_number,
                value=None,
                source_ref="test:withdrawn",
                status="withdrawn",
                source_published_at=None,
                ingested_at=now,
                payload_hash=quote_revision_payload_hash(
                    value=None,
                    source_ref="test:withdrawn",
                    status="withdrawn",
                ),
                is_current=True,
                superseded_at=None,
            )
        )
        session.commit()


def _book(
    factory,
    *,
    pairs: list[tuple[str, str]],
    start_date: date = date(2026, 7, 1),
    end_date: date = date(2026, 7, 13),
):
    with factory() as session:
        return resolve_canonical_fx_window_book_in_session(
            session,
            currency_pairs=pairs,
            start_date=start_date,
            end_date=end_date,
            freshness_policy=CARRY_5,
            consumer_policy_version=CONSUMER_POLICY_VERSION,
        )


def test_direct_inverse_cross_and_identity_paths_keep_decimal_lineage_and_stable_fingerprints(
    fx_registry,
) -> None:
    _, factory = fx_registry
    _create_fx_instruments(factory)
    _upsert_fx(
        factory,
        quote_currency="HKD",
        point_date=date(2026, 7, 10),
        value="7.800000000000000000000001",
        source_ref="issuer:usd-hkd",
    )
    _upsert_fx(
        factory,
        quote_currency="CNY",
        point_date=date(2026, 7, 12),
        value="7.200000000000000000000002",
        source_ref="issuer:usd-cny",
    )
    pairs = [
        ("USD", "HKD"),
        ("HKD", "USD"),
        ("HKD", "CNY"),
        ("CNY", "CNY"),
    ]
    book = _book(factory, pairs=pairs)

    direct = book.rate_at("USD", "HKD", date(2026, 7, 13))
    inverse = book.rate_at("HKD", "USD", date(2026, 7, 13))
    cross = book.rate_at("HKD", "CNY", date(2026, 7, 13))
    identity = book.rate_at("CNY", "CNY", date(2026, 7, 13))

    hkd_rate = Decimal("7.800000000000000000000001")
    cny_rate = Decimal("7.200000000000000000000002")
    assert direct.path_kind == "direct"
    assert direct.rate == hkd_rate
    assert inverse.path_kind == "inverse"
    assert inverse.rate == Decimal("1") / hkd_rate
    assert inverse.legs[0].inverted is True
    assert cross.path_kind == "cross"
    assert cross.rate == cny_rate / hkd_rate
    assert cross.effective_as_of_date == date(2026, 7, 10)
    assert cross.reliability_status == "qualified"
    assert [leg.operation for leg in cross.legs] == ["divide", "multiply"]
    assert [
        leg.calculation_dependency.path_position for leg in cross.legs
    ] == [1, 2]
    assert all(
        leg.calculation_dependency.quote_calculation_dependency is not None
        for leg in cross.legs
    )
    assert all(
        leg.calculation_dependency.quote_window_calculation_dependency.quote_series_id
        for leg in cross.legs
    )
    assert all(
        leg.quote_resolution is not None
        and leg.quote_resolution.metric_family == "fx"
        and leg.quote_resolution.quote_basis == "spot"
        and leg.quote_resolution.role == "valuation"
        for leg in cross.legs
    )
    assert identity.path_kind == "identity"
    assert identity.rate == Decimal("1")
    assert identity.effective_as_of_date == date(2026, 7, 13)

    assert direct.calculation_dependency.fingerprint != inverse.calculation_dependency.fingerprint
    assert cross.calculation_dependency.fingerprint != book.rate_at(
        "HKD", "CNY", date(2026, 7, 12)
    ).calculation_dependency.fingerprint

    reordered_book = _book(factory, pairs=list(reversed(pairs)))
    reordered_cross = reordered_book.rate_at("HKD", "CNY", date(2026, 7, 13))
    assert (
        reordered_cross.calculation_dependency.fingerprint
        == cross.calculation_dependency.fingerprint
    )
    dependency_json = cross.calculation_dependency.model_dump(mode="json")
    assert "source_ref" not in json.dumps(
        dependency_json,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert cross.resolver_strategy_version == CANONICAL_FX_RESOLVER_STRATEGY_VERSION


def test_five_calendar_days_are_adoptable_but_day_six_fails_closed(
    fx_registry,
) -> None:
    _, factory = fx_registry
    _create_fx_instruments(factory)
    _upsert_fx(
        factory,
        quote_currency="HKD",
        point_date=date(2026, 7, 7),
        value="7.8",
    )
    book = _book(factory, pairs=[("USD", "HKD")])

    day_five = book.rate_at("USD", "HKD", date(2026, 7, 12))
    day_six = book.rate_at("USD", "HKD", date(2026, 7, 13))

    assert day_five.resolution_status == "resolved"
    assert day_five.rate == Decimal("7.8")
    assert day_five.reliability_status == "qualified"
    assert day_five.reason_codes == ["carried_forward_fx_leg"]
    assert day_six.resolution_status == "unavailable"
    assert day_six.rate is None
    assert day_six.freshness_status == "late"
    assert day_six.reason_codes == ["late_fx_leg", "unavailable_fx_leg"]
    assert "freshness_limit_exceeded" in day_six.legs[0].reason_codes


@pytest.mark.parametrize(
    ("status", "expected_reason"),
    [
        ("partial", "partial_series"),
        ("rejected", "rejected_observation"),
        ("withdrawn", "withdrawn_observation"),
    ],
)
def test_later_non_complete_current_revision_blocks_prior_complete_revision(
    fx_registry,
    status: str,
    expected_reason: str,
) -> None:
    _, factory = fx_registry
    _create_fx_instruments(factory)
    point_date = date(2026, 7, 12)
    _upsert_fx(
        factory,
        quote_currency="HKD",
        point_date=point_date,
        value="7.8",
        status="complete",
    )
    if status == "withdrawn":
        _withdraw_current_fx_observation(
            factory,
            quote_currency="HKD",
            point_date=point_date,
        )
    else:
        _upsert_fx(
            factory,
            quote_currency="HKD",
            point_date=point_date,
            value="7.81",
            status=status,
            source_ref=f"test:{status}",
        )
    book = _book(factory, pairs=[("USD", "HKD")])

    resolution = book.rate_at("USD", "HKD", point_date)

    assert resolution.resolution_status == "unavailable"
    assert resolution.rate is None
    assert resolution.reason_codes == ["unavailable_fx_leg"]
    leg = resolution.legs[0]
    assert expected_reason in leg.reason_codes
    assert leg.quote_resolution is not None
    assert leg.quote_resolution.value is None
    assert leg.quote_resolution.revision_number == 2
    dependency = leg.calculation_dependency.quote_calculation_dependency
    assert dependency is not None
    assert dependency.revision_id == leg.quote_resolution.revision_id
    assert dependency.payload_hash == leg.quote_resolution.payload_hash


def test_cross_fails_closed_when_either_leg_is_late_or_missing(fx_registry) -> None:
    _, factory = fx_registry
    _create_fx_instruments(factory)
    _upsert_fx(
        factory,
        quote_currency="HKD",
        point_date=date(2026, 7, 13),
        value="7.8",
    )
    _upsert_fx(
        factory,
        quote_currency="CNY",
        point_date=date(2026, 7, 7),
        value="7.2",
    )
    book = _book(factory, pairs=[("HKD", "CNY")])

    late_cross = book.rate_at("HKD", "CNY", date(2026, 7, 13))

    assert late_cross.resolution_status == "unavailable"
    assert late_cross.rate is None
    assert late_cross.freshness_status == "late"
    assert late_cross.reason_codes == ["late_fx_leg", "unavailable_fx_leg"]
    assert len(late_cross.legs) == 2
    assert late_cross.legs[0].resolution_status == "resolved"
    assert late_cross.legs[1].resolution_status == "unavailable"

    with factory() as session:
        session.query(QuoteObservationRevision).filter(
            QuoteObservationRevision.observation_id.in_(
                select(QuoteObservation.observation_id)
                .join(
                    QuoteSeries,
                    QuoteSeries.quote_series_id == QuoteObservation.quote_series_id,
                )
                .where(QuoteSeries.instrument_id == "fx-usd-cny")
            )
        ).delete(synchronize_session=False)
        session.query(QuoteObservation).filter(
            QuoteObservation.quote_series_id.in_(
                select(QuoteSeries.quote_series_id).where(
                    QuoteSeries.instrument_id == "fx-usd-cny"
                )
            )
        ).delete(synchronize_session=False)
        session.commit()
    missing_book = _book(factory, pairs=[("HKD", "CNY")])
    missing_cross = missing_book.rate_at("HKD", "CNY", date(2026, 7, 13))
    assert missing_cross.resolution_status == "unavailable"
    assert missing_cross.freshness_status == "missing"
    assert missing_cross.reason_codes == ["missing_fx_leg", "unavailable_fx_leg"]


def test_locked_window_has_bounded_queries_and_rate_at_never_queries(fx_registry) -> None:
    engine, factory = fx_registry
    _create_fx_instruments(factory)
    for point_date, hkd_rate, cny_rate in (
        (date(2026, 7, 1), "7.79", "7.19"),
        (date(2026, 7, 7), "7.80", "7.20"),
        (date(2026, 7, 13), "7.81", "7.21"),
    ):
        _upsert_fx(
            factory,
            quote_currency="HKD",
            point_date=point_date,
            value=hkd_rate,
        )
        _upsert_fx(
            factory,
            quote_currency="CNY",
            point_date=point_date,
            value=cny_rate,
        )

    statements: list[str] = []

    def capture_statement(*args) -> None:
        statements.append(str(args[2]))

    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        with factory() as session:
            book = resolve_canonical_fx_window_book_in_session(
                session,
                currency_pairs=[
                    ("USD", "HKD"),
                    ("HKD", "USD"),
                    ("HKD", "CNY"),
                    ("CNY", "HKD"),
                ],
                start_date=date(2026, 7, 1),
                end_date=date(2026, 7, 13),
                freshness_policy=CARRY_5,
                consumer_policy_version=CONSUMER_POLICY_VERSION,
            )
        construction_query_count = len(statements)
        for requested_date in (
            date(2026, 7, 1),
            date(2026, 7, 7),
            date(2026, 7, 13),
        ):
            assert book.rate_at(
                "HKD", "CNY", requested_date
            ).resolution_status == "resolved"
        assert len(statements) == construction_query_count
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)

    assert construction_query_count == 5
    assert sum("quote_observation" in statement for statement in statements) == 2


def test_pair_lock_and_explicit_policy_version_fail_closed(fx_registry) -> None:
    _, factory = fx_registry
    _create_fx_instruments(factory)
    _upsert_fx(
        factory,
        quote_currency="HKD",
        point_date=date(2026, 7, 13),
        value="7.8",
    )
    book = _book(factory, pairs=[("USD", "HKD")])

    with pytest.raises(TypeError):
        book.paths[("HKD", "USD")] = None  # type: ignore[index]

    unlocked = book.rate_at("HKD", "USD", date(2026, 7, 13))
    assert unlocked.resolution_status == "unavailable"
    assert unlocked.reason_codes == ["currency_pair_not_locked"]

    with factory() as session:
        with pytest.raises(CanonicalFxResolverError) as error:
            resolve_canonical_fx_window_book_in_session(
                session,
                currency_pairs=[("USD", "HKD")],
                start_date=date(2026, 7, 1),
                end_date=date(2026, 7, 13),
                freshness_policy=CARRY_5,
                consumer_policy_version="",
            )
    assert error.value.reason_code == "missing_consumer_policy_version"

    unsupported_book = _book(factory, pairs=[("USD", "EUR")])
    unsupported = unsupported_book.rate_at("USD", "EUR", date(2026, 7, 13))
    assert unsupported.resolution_status == "unavailable"
    assert unsupported.reason_codes == ["unsupported_currency_pair"]


def test_cross_path_accepts_inverse_maintained_pivot_legs(
    fx_registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, factory = fx_registry
    inverse_mapping = {
        ("HKD", "USD"): "fx-hkd-usd",
        ("CNY", "USD"): "fx-cny-usd",
    }
    monkeypatch.setattr(
        canonical_fx_module,
        "MAINTAINED_FX_INSTRUMENTS",
        inverse_mapping,
    )
    for (base_currency, quote_currency), instrument_id in inverse_mapping.items():
        created = shared_store.create_instrument(
            factory,
            instrument_name=f"{base_currency}/{quote_currency} Spot",
            instrument_type="fx",
            currency=quote_currency,
            identifiers=[
                {
                    "identifier_type": "internal",
                    "identifier_value": instrument_id,
                    "is_primary": True,
                }
            ],
        )
        assert created["instrument_id"] == instrument_id
        result = shared_store.upsert_market_data(
            factory,
            instrument_id=instrument_id,
            metric_family="fx",
            quote_basis="spot",
            as_of_date=date(2026, 7, 13),
            value="0.1282" if base_currency == "HKD" else "0.1389",
            currency=quote_currency,
            source_ref=f"test:{instrument_id}",
            status="complete",
        )
        assert result is not None

    book = _book(factory, pairs=[("HKD", "CNY")])
    cross = book.rate_at("HKD", "CNY", date(2026, 7, 13))

    assert cross.resolution_status == "resolved"
    assert cross.rate == Decimal("0.1282") / Decimal("0.1389")
    assert [leg.instrument_id for leg in cross.legs] == [
        "fx-hkd-usd",
        "fx-cny-usd",
    ]
    assert [leg.operation for leg in cross.legs] == ["multiply", "divide"]
    assert [leg.inverted for leg in cross.legs] == [False, True]


def test_resolution_and_dependency_models_reject_inconsistent_payloads(
    fx_registry,
) -> None:
    _, factory = fx_registry
    _create_fx_instruments(factory)
    _upsert_fx(
        factory,
        quote_currency="HKD",
        point_date=date(2026, 7, 13),
        value="7.8",
    )
    _upsert_fx(
        factory,
        quote_currency="CNY",
        point_date=date(2026, 7, 13),
        value="7.2",
    )
    resolution = _book(
        factory,
        pairs=[("HKD", "CNY")],
    ).rate_at("HKD", "CNY", date(2026, 7, 13))

    top_payload = resolution.model_dump(mode="python")
    for field_name, inconsistent_value in (
        ("base_currency", "USD"),
        ("requested_as_of_date", date(2026, 7, 12)),
        ("path_kind", "direct"),
        (
            "freshness_policy",
            QuoteFreshnessPolicy(
                policy_version="canonical_quote_freshness.v1",
                mode="calendar_day_carry_forward",
                max_age_days=4,
            ),
        ),
    ):
        inconsistent = dict(top_payload)
        inconsistent[field_name] = inconsistent_value
        with pytest.raises(ValidationError):
            CanonicalFxResolution.model_validate(inconsistent)

    dependency_payload = resolution.calculation_dependency.model_dump(mode="python")
    reversed_legs = dict(dependency_payload)
    reversed_legs["legs"] = list(reversed(dependency_payload["legs"]))
    with pytest.raises(ValidationError):
        CanonicalFxCalculationDependency.model_validate(reversed_legs)

    changed_policy = dict(dependency_payload)
    changed_policy["consumer_policy_version"] = "tampered-policy.v1"
    with pytest.raises(ValidationError):
        CanonicalFxCalculationDependency.model_validate(changed_policy)
