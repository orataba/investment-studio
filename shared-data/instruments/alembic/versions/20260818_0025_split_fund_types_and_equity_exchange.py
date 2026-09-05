"""Split funds by operating model and add canonical equity exchange identity.

Revision ID: 20260818_0025
Revises: 20260812_0024
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
import json
import re

from alembic import op
import sqlalchemy as sa


revision: str = "20260818_0025"
down_revision: str | None = "20260812_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


INSTRUMENT_TYPES = (
    "public_fund",
    "private_fund",
    "etf",
    "index",
    "equity",
    "cash",
    "fx",
    "other",
)
SUPPORTED_EQUITY_EXCHANGES = ("XNAS", "XNYS", "XASE", "XHKG", "XSHG", "XSHE")
FUND_NAV_INSTRUMENT_TRIGGER = "trg_instrument_fund_nav_contract"
FUND_NAV_INSTRUMENT_FUNCTION = "enforce_instrument_fund_nav_contract"
FUND_NAV_FUNCTIONS = (
    "enforce_fund_nav_event_instrument_contract",
    "enforce_fund_nav_reinvestment_evidence_contract",
    "enforce_fund_nav_projection_run_contract",
    "enforce_fund_nav_projection_run_event_contract",
    "enforce_fund_nav_projection_run_reinvestment_evidence_contract",
    "enforce_fund_nav_factor_event_contract",
    "enforce_fund_nav_current_projection_contract",
    "enforce_fund_nav_market_data_factor_contract",
    FUND_NAV_INSTRUMENT_FUNCTION,
)
PRIVATE_FUND_NAME_MARKERS = ("私募", "量化对冲", "CTA")
FUND_LEGAL_NAME_MARKERS = ("私募证券投资基金", "私募投资基金", "证券投资基金")


def _watermark() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json_object(value: object) -> dict[str, object]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return dict(value) if isinstance(value, dict) else {}


def _fund_name_stem(value: object) -> str:
    normalized = "".join(str(value or "").strip().upper().split())
    for marker in FUND_LEGAL_NAME_MARKERS:
        normalized = normalized.replace(marker, "")
    return normalized


def _canonical_fund_trigger_sql(sql: str) -> str:
    result = re.sub(
        r"instrument_type\s*<>\s*'fund'(?:::text)?",
        "instrument_type NOT IN ('public_fund', 'private_fund')",
        sql,
    )
    result = re.sub(
        r"instrument_type\s*=\s*'fund'(?:::text)?",
        "instrument_type IN ('public_fund', 'private_fund')",
        result,
    )
    return result


def _sqlite_triggers(connection: sa.Connection) -> list[tuple[str, str]]:
    if connection.dialect.name != "sqlite":
        return []
    return [
        (str(row.name), str(row.sql))
        for row in connection.execute(
            sa.text(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'trigger' AND sql IS NOT NULL ORDER BY name"
            )
        )
    ]


def _drop_sqlite_triggers(
    connection: sa.Connection,
    triggers: list[tuple[str, str]],
) -> None:
    for name, _sql in triggers:
        quoted = '"' + name.replace('"', '""') + '"'
        connection.execute(sa.text(f"DROP TRIGGER IF EXISTS {quoted}"))


def _restore_sqlite_triggers(
    connection: sa.Connection,
    triggers: list[tuple[str, str]],
) -> None:
    for _name, sql in triggers:
        connection.exec_driver_sql(_canonical_fund_trigger_sql(sql))


def _postgres_fund_function_definitions(connection: sa.Connection) -> list[str]:
    if connection.dialect.name != "postgresql":
        return []
    return [
        str(value)
        for value in connection.execute(
            sa.text(
                "SELECT pg_get_functiondef(proc.oid) "
                "FROM pg_proc AS proc "
                "JOIN pg_namespace AS namespace ON namespace.oid = proc.pronamespace "
                "WHERE proc.proname IN :function_names "
                "AND namespace.nspname = current_schema() "
                "ORDER BY proc.proname"
            ).bindparams(sa.bindparam("function_names", expanding=True)),
            {"function_names": list(FUND_NAV_FUNCTIONS)},
        ).scalars()
    ]


def _drop_fund_nav_instrument_trigger(connection: sa.Connection) -> None:
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text(
                f"DROP TRIGGER IF EXISTS {FUND_NAV_INSTRUMENT_TRIGGER} ON instrument"
            )
        )


def _restore_postgres_fund_functions(
    connection: sa.Connection,
    definitions: list[str],
) -> None:
    if connection.dialect.name != "postgresql":
        return
    for definition in definitions:
        # pg_get_functiondef may return PL/pgSQL declarations such as
        # ``fund_nav_event%ROWTYPE``. Psycopg uses ``%`` for placeholders, so
        # literal percent signs must be doubled when executing raw driver SQL.
        sql = _canonical_fund_trigger_sql(definition).replace("%", "%%")
        connection.exec_driver_sql(sql)
    connection.execute(
        sa.text(
            f"CREATE TRIGGER {FUND_NAV_INSTRUMENT_TRIGGER} "
            "BEFORE UPDATE OF instrument_type ON instrument "
            "FOR EACH ROW EXECUTE FUNCTION "
            f"{FUND_NAV_INSTRUMENT_FUNCTION}()"
        )
    )


def _fund_classifications(
    connection: sa.Connection,
) -> dict[str, str]:
    rows = list(
        connection.execute(
            sa.text(
                "SELECT instrument_id, instrument_name, source_settings_json, "
                "lifecycle_state_json FROM instrument "
                "WHERE instrument_type = 'fund' ORDER BY instrument_id"
            )
        ).mappings()
    )
    identifiers: dict[str, list[str]] = {}
    for row in connection.execute(
        sa.text(
            "SELECT instrument_id, identifier_value FROM instrument_identifier "
            "ORDER BY instrument_id, is_primary DESC, instrument_identifier_id"
        )
    ).mappings():
        identifiers.setdefault(str(row["instrument_id"]), []).append(
            str(row["identifier_value"] or "").strip()
        )

    classifications: dict[str, str] = {}
    canonical_by_alias: dict[str, str] = {}
    names: dict[str, str] = {}
    for row in rows:
        instrument_id = str(row["instrument_id"])
        name = str(row["instrument_name"] or "").strip()
        names[instrument_id] = name
        settings = _json_object(row["source_settings_json"])
        lifecycle = _json_object(row["lifecycle_state_json"])
        canonical_id = str(lifecycle.get("canonical_instrument_id") or "").strip()
        if canonical_id:
            canonical_by_alias[instrument_id] = canonical_id

        source_mode = str(settings.get("source_mode") or "").strip().lower()
        source_email = str(settings.get("source_email") or "").strip()
        api_profile = str(settings.get("source_api_profile") or "").strip().lower()
        settings_text = json.dumps(settings, ensure_ascii=False, sort_keys=True)
        values = identifiers.get(instrument_id, [])

        if source_mode == "email" or source_email:
            classifications[instrument_id] = "private_fund"
        elif api_profile == "tushare":
            classifications[instrument_id] = "public_fund"
        elif (
            any(marker in name.upper() for marker in PRIVATE_FUND_NAME_MARKERS)
            or "私募" in settings_text
        ):
            classifications[instrument_id] = "private_fund"
        elif "公募" in name or any(value.upper().endswith(".OF") for value in values):
            classifications[instrument_id] = "public_fund"

    changed = True
    while changed:
        changed = False
        for alias_id, canonical_id in canonical_by_alias.items():
            if alias_id not in classifications and canonical_id in classifications:
                classifications[alias_id] = classifications[canonical_id]
                changed = True
        classification_by_name_stem: dict[str, str] = {}
        for instrument_id, instrument_type in classifications.items():
            stem = _fund_name_stem(names.get(instrument_id))
            previous = classification_by_name_stem.get(stem)
            if stem and previous is not None and previous != instrument_type:
                raise RuntimeError(
                    f"Fund name stem {stem} maps to both {previous} and {instrument_type}."
                )
            if stem:
                classification_by_name_stem[stem] = instrument_type
        for instrument_id, name in names.items():
            if instrument_id in classifications:
                continue
            instrument_type = classification_by_name_stem.get(_fund_name_stem(name))
            if instrument_type is not None:
                classifications[instrument_id] = instrument_type
                changed = True

    unresolved = [
        str(row["instrument_id"])
        for row in rows
        if str(row["instrument_id"]) not in classifications
    ]
    if unresolved:
        raise RuntimeError(
            "Fund type split has unresolved Registry identities: " + ", ".join(unresolved)
        )
    return classifications


def _equity_identity(identifier_values: list[str]) -> tuple[str, str]:
    for raw_value in identifier_values:
        value = raw_value.strip().upper()
        if value.endswith(".SH"):
            return "XSHG", value[:-3] + ".SS"
        if value.endswith(".SS"):
            return "XSHG", value
        if value.endswith(".SZ"):
            return "XSHE", value
        if value.endswith(".HK"):
            return "XHKG", value
    raise RuntimeError(
        "Existing equity lacks a supported exchange-qualified identifier: "
        + ", ".join(identifier_values)
    )


def _migrate_equities(connection: sa.Connection, changed_at: str) -> None:
    rows = list(
        connection.execute(
            sa.text(
                "SELECT instrument_id, source_settings_json FROM instrument "
                "WHERE instrument_type = 'equity' ORDER BY instrument_id"
            )
        ).mappings()
    )
    for row in rows:
        instrument_id = str(row["instrument_id"])
        identifier_rows = list(
            connection.execute(
                sa.text(
                    "SELECT identifier_value FROM instrument_identifier "
                    "WHERE instrument_id = :instrument_id "
                    "ORDER BY is_primary DESC, instrument_identifier_id"
                ),
                {"instrument_id": instrument_id},
            ).scalars()
        )
        exchange_code, fmp_symbol = _equity_identity([str(value) for value in identifier_rows])
        settings = _json_object(row["source_settings_json"])
        settings.update(
            {
                "source_mode": "api",
                "source_location": "FMP API",
                "source_api_profile": "fmp",
                "expected_frequency": "daily",
                "market_calendar": exchange_code,
                "release_lag_days": 0,
                "return_semantics": "price_return",
            }
        )
        refresh_status = {
            "status": "idle",
            "message": "Awaiting first FMP EOD refresh after provider cutover.",
            "requested_at": None,
            "requested_by": None,
            "mode": "api",
            "last_successful_requested_at": None,
        }
        connection.execute(
            sa.text(
                "DELETE FROM instrument_market_data WHERE instrument_id = :instrument_id"
            ),
            {"instrument_id": instrument_id},
        )
        connection.execute(
            sa.text(
                "DELETE FROM instrument_price_bar WHERE instrument_id = :instrument_id"
            ),
            {"instrument_id": instrument_id},
        )
        connection.execute(
            sa.text(
                "UPDATE instrument SET exchange_code = :exchange_code, "
                "source_settings_json = :settings, "
                "refresh_status_json = :refresh_status, "
                "market_data_updated_at = NULL, "
                "calculation_inputs_updated_at = :changed_at "
                "WHERE instrument_id = :instrument_id"
            ),
            {
                "instrument_id": instrument_id,
                "exchange_code": exchange_code,
                "settings": json.dumps(settings, ensure_ascii=False),
                "refresh_status": json.dumps(refresh_status, ensure_ascii=False),
                "changed_at": changed_at,
            },
        )
        connection.execute(
            sa.text(
                "UPDATE instrument_identifier SET identifier_type = 'exchange_ticker' "
                "WHERE instrument_id = :instrument_id AND identifier_type = 'ticker'"
            ),
            {"instrument_id": instrument_id},
        )
        provider_owner = connection.execute(
            sa.text(
                "SELECT instrument_id FROM instrument_identifier "
                "WHERE identifier_type = 'provider_symbol' "
                "AND identifier_value = :provider_symbol"
            ),
            {"provider_symbol": f"fmp:{fmp_symbol}"},
        ).scalar_one_or_none()
        if provider_owner is not None and str(provider_owner) != instrument_id:
            raise RuntimeError(
                f"FMP provider symbol {fmp_symbol} already belongs to {provider_owner}."
            )
        if provider_owner is None:
            connection.execute(
                sa.text(
                    "INSERT INTO instrument_identifier "
                    "(instrument_id, identifier_type, identifier_value, is_primary) "
                    "VALUES (:instrument_id, 'provider_symbol', :provider_symbol, false)"
                ),
                {
                    "instrument_id": instrument_id,
                    "provider_symbol": f"fmp:{fmp_symbol}",
                },
            )


def _replace_instrument_type_constraint() -> None:
    with op.batch_alter_table("instrument") as batch_op:
        batch_op.drop_constraint("instrument_type_contract", type_="check")


def _add_registry_constraints() -> None:
    quoted_types = ", ".join(f"'{value}'" for value in INSTRUMENT_TYPES)
    quoted_exchanges = ", ".join(f"'{value}'" for value in SUPPORTED_EQUITY_EXCHANGES)
    with op.batch_alter_table("instrument") as batch_op:
        batch_op.create_check_constraint(
            "instrument_type_contract",
            f"instrument_type IN ({quoted_types})",
        )
        batch_op.create_check_constraint(
            "instrument_exchange_contract",
            "((instrument_type = 'equity' AND exchange_code IN "
            f"({quoted_exchanges})) OR "
            "(instrument_type <> 'equity' AND exchange_code IS NULL))",
        )


def upgrade() -> None:
    connection = op.get_bind()
    sqlite_triggers = _sqlite_triggers(connection)
    postgres_function_definitions = _postgres_fund_function_definitions(connection)
    _drop_sqlite_triggers(connection, sqlite_triggers)
    _drop_fund_nav_instrument_trigger(connection)

    with op.batch_alter_table("instrument") as batch_op:
        batch_op.add_column(sa.Column("exchange_code", sa.String(length=4), nullable=True))
    _replace_instrument_type_constraint()

    changed_at = _watermark()
    for instrument_id, instrument_type in _fund_classifications(connection).items():
        connection.execute(
            sa.text(
                "UPDATE instrument SET instrument_type = :instrument_type, "
                "calculation_inputs_updated_at = :changed_at "
                "WHERE instrument_id = :instrument_id"
            ),
            {
                "instrument_id": instrument_id,
                "instrument_type": instrument_type,
                "changed_at": changed_at,
            },
        )
    _migrate_equities(connection, changed_at)

    remaining = connection.execute(
        sa.text("SELECT count(*) FROM instrument WHERE instrument_type = 'fund'")
    ).scalar_one()
    if int(remaining) != 0:
        raise RuntimeError("Legacy fund instrument_type remains after migration")

    _add_registry_constraints()
    _restore_sqlite_triggers(connection, sqlite_triggers)
    _restore_postgres_fund_functions(connection, postgres_function_definitions)


def downgrade() -> None:
    raise RuntimeError(
        "Revision 20260818_0025 removes the ambiguous fund type and establishes "
        "FMP equity identity. Restore the pre-migration database backup instead."
    )
