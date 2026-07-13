from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime, time
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

import psycopg


PORTFOLIO_BACKEND = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PORTFOLIO_BACKEND.parents[2]
INSTRUMENT_CORE = WORKSPACE_ROOT / "packages" / "instrument-core" / "python"
for import_root in (PORTFOLIO_BACKEND, INSTRUMENT_CORE):
    import_root_text = str(import_root)
    if import_root_text not in sys.path:
        sys.path.insert(0, import_root_text)

from portfolio_app.services.transaction_revisions import (  # noqa: E402
    TransactionFactPayload,
    tombstone_payload_hash,
    transaction_payload_hash,
)


AUDIT_PATH = WORKSPACE_ROOT / "infra" / "scripts" / "audit_live_data.py"
AUDIT_MODULE_NAME = "portfolio_ops_audit_live_data_test"
AUDIT_SPEC = importlib.util.spec_from_file_location(AUDIT_MODULE_NAME, AUDIT_PATH)
assert AUDIT_SPEC is not None and AUDIT_SPEC.loader is not None
audit = importlib.util.module_from_spec(AUDIT_SPEC)
sys.modules[AUDIT_MODULE_NAME] = audit
AUDIT_SPEC.loader.exec_module(audit)


def _transfer_leg(
    *,
    transaction_id: str,
    transaction_type: str,
    account_id: str,
    counterparty_account_id: str,
    transfer_group_id: str = "transfer-group-1",
    revision_group_id: str = "create-group-1",
) -> dict[str, object]:
    facts = TransactionFactPayload(
        transaction_type=transaction_type,
        trade_date=date(2026, 4, 16),
        trade_time=time(9, 30),
        trade_at=datetime(2026, 4, 16, 9, 30, tzinfo=UTC),
        trade_timezone="UTC",
        trade_time_is_estimated=False,
        settlement_date=date(2026, 4, 16),
        account_id=account_id,
        gross_amount="100.00000000",
        fees="0.00000000",
        taxes="0.00000000",
        currency="USD",
        transfer_scope="internal_portfolio",
        transfer_object_type="cash",
        transfer_group_id=transfer_group_id,
        counterparty_account_id=counterparty_account_id,
        note="Canonical transfer pair",
    )
    return {
        "revision_id": f"revision-{transaction_id}-1",
        "portfolio_id": "portfolio-1",
        "transaction_id": transaction_id,
        "revision_number": 1,
        "revision_group_id": revision_group_id,
        "revision_kind": "create",
        "is_tombstone": False,
        "payload_schema_version": "transaction-revision.v1",
        "payload_hash": transaction_payload_hash(facts),
        **facts.as_record_values(),
    }


def _valid_transfer_pair(
    *,
    transaction_suffix: str = "1",
    transfer_group_id: str = "transfer-group-1",
) -> list[dict[str, object]]:
    return [
        _transfer_leg(
            transaction_id=f"transfer-out-{transaction_suffix}",
            transaction_type="transfer_out",
            account_id="cash-main",
            counterparty_account_id="cash-reserve",
            transfer_group_id=transfer_group_id,
        ),
        _transfer_leg(
            transaction_id=f"transfer-in-{transaction_suffix}",
            transaction_type="transfer_in",
            account_id="cash-reserve",
            counterparty_account_id="cash-main",
            transfer_group_id=transfer_group_id,
        ),
    ]


def _tombstone(
    live_revision: dict[str, object],
    *,
    revision_group_id: str = "delete-group-1",
) -> dict[str, object]:
    return {
        "revision_id": f"revision-{live_revision['transaction_id']}-2",
        "portfolio_id": live_revision["portfolio_id"],
        "transaction_id": live_revision["transaction_id"],
        "revision_number": 2,
        "revision_group_id": revision_group_id,
        "revision_kind": "delete",
        "is_tombstone": True,
        "payload_schema_version": "transaction-revision.v1",
        "payload_hash": tombstone_payload_hash(),
        **{field_name: None for field_name in audit.TRANSACTION_FACT_FIELDS},
    }


def test_payload_hash_recomputation_matches_runtime_canonical_rules() -> None:
    revision = _valid_transfer_pair()[0]
    rich_facts = TransactionFactPayload(
        transaction_type="buy",
        trade_date=date(2026, 7, 14),
        trade_time=time(15, 0, 1, 123456),
        trade_at=datetime.fromisoformat("2026-07-14T15:00:01.123456+08:00"),
        trade_timezone="Asia/Shanghai",
        trade_time_is_estimated=False,
        settlement_date=date(2026, 7, 16),
        entitlement_date=date(2026, 7, 13),
        acquisition_date=date(2026, 7, 12),
        account_id="brokerage-main",
        settlement_cash_account_id="cash-main",
        instrument_id="instrument-rich",
        instrument_snapshot_json={
            "instrument_id": "instrument-rich",
            "nested": {"unicode": "均成", "float": 1.25},
            "identifiers": ["ABC", 7],
        },
        quantity="1.234567890123",
        price="12.345678901234",
        gross_amount="15.24157875",
        counter_amount="100.12345678",
        fx_rate="7.123456789012345678",
        fees="0.12345678",
        taxes="0.00000001",
        currency="usd",
        note="  exact note whitespace  ",
    )
    rich_revision = {
        "is_tombstone": False,
        "payload_hash": transaction_payload_hash(rich_facts),
        **rich_facts.as_record_values(),
    }

    assert audit._recompute_transaction_payload_hash(revision) == revision["payload_hash"]
    assert (
        audit._recompute_transaction_payload_hash(rich_revision)
        == rich_revision["payload_hash"]
    )
    assert (
        audit._recompute_transaction_payload_hash(_tombstone(revision))
        == tombstone_payload_hash()
    )
    assert audit._transaction_payload_hash_recomputation_check([revision]).status == "pass"

    tampered = {**revision, "payload_hash": f"sha256:{'0' * 64}"}
    failed = audit._transaction_payload_hash_recomputation_check([tampered])
    assert failed.status == "fail"
    assert failed.value == 1


def test_current_transfer_audit_rejects_orphans_nonreciprocity_and_mismatch() -> None:
    pair = _valid_transfer_pair()
    assert audit._transaction_current_transfer_check(pair).status == "pass"

    orphan = audit._transaction_current_transfer_check(pair[:1])
    assert orphan.status == "fail"
    assert orphan.value == 1

    nonreciprocal = deepcopy(pair)
    nonreciprocal[1]["counterparty_account_id"] = "cash-reserve"
    assert audit._transaction_current_transfer_check(nonreciprocal).status == "fail"

    mismatched = deepcopy(pair)
    mismatched[1]["gross_amount"] += 1  # type: ignore[operator]
    assert audit._transaction_current_transfer_check(mismatched).status == "fail"


def test_history_transfer_audit_rejects_group_reuse_and_amendment() -> None:
    pair = _valid_transfer_pair()
    assert audit._transaction_history_transfer_check(pair).status == "pass"

    reused = pair + _valid_transfer_pair(
        transaction_suffix="2",
        transfer_group_id="transfer-group-1",
    )
    reuse_check = audit._transaction_history_transfer_check(reused)
    assert reuse_check.status == "fail"
    assert reuse_check.value == 1

    amended = deepcopy(pair)
    amendment = deepcopy(pair[0])
    amendment.update(
        {
            "revision_id": "revision-transfer-out-1-2",
            "revision_number": 2,
            "revision_group_id": "amend-group-1",
            "revision_kind": "amend",
            "transfer_group_id": None,
            "transaction_type": "deposit",
        }
    )
    amended.append(amendment)
    assert audit._transaction_history_transfer_check(amended).status == "fail"


def test_history_transfer_audit_requires_atomic_pair_deletion() -> None:
    pair = _valid_transfer_pair()
    deleted = pair + [_tombstone(leg) for leg in pair]
    assert audit._transaction_history_transfer_check(deleted).status == "pass"

    split_delete = deepcopy(deleted)
    split_delete[-1]["revision_group_id"] = "delete-group-2"
    assert audit._transaction_history_transfer_check(split_delete).status == "fail"


class _SchemaGateCursor:
    def __init__(
        self,
        *,
        portfolio_head: str = "20260713_0038",
        object_violations: int = 0,
        authority_violations: int = 0,
    ) -> None:
        self.portfolio_head = portfolio_head
        self.object_violations = object_violations
        self.authority_violations = authority_violations
        self.rows: list[tuple[Any, ...]] = []

    def execute(self, query: str) -> None:
        if "WITH required(component, relation_name)" in query:
            self.rows = [
                (
                    "instrument_registry",
                    "instrument_registry.alembic_version",
                    object(),
                ),
                ("portfolio", "portfolio.alembic_version", object()),
                ("watchlist", "watchlist.alembic_version", object()),
            ]
            return
        if "FROM instrument_registry.alembic_version" in query:
            self.rows = [
                ("instrument_registry", 1, "20260713_0011"),
                ("portfolio", 1, self.portfolio_head),
                ("watchlist", 1, "20260713_0030"),
            ]
            return
        if "WITH expected(relation_name, relation_kind)" in query:
            self.rows = [(self.object_violations,)]
            return
        if "WITH expected_function(" in query:
            self.rows = [(self.authority_violations,)]
            return
        raise AssertionError(f"unexpected schema-gate query: {query}")

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self.rows)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None


def test_old_head_returns_structured_fail_and_skips_schema_queries() -> None:
    checks, ready = audit._schema_capability_gate(
        _SchemaGateCursor(portfolio_head="20260713_0036")
    )

    assert ready is False
    checks_by_name = {check.name: check for check in checks}
    assert checks_by_name["database_migration_version_tables"].status == "pass"
    assert checks_by_name["database_migration_heads"].status == "fail"
    assert checks_by_name["portfolio_transaction_revision_ledger_objects"].status == "skip"
    assert checks_by_name["portfolio_transaction_0037_database_authority"].status == "skip"
    assert checks_by_name["database_schema_dependent_checks"].status == "skip"


def test_missing_0037_objects_returns_fail_and_skip() -> None:
    checks, ready = audit._schema_capability_gate(
        _SchemaGateCursor(object_violations=1)
    )

    assert ready is False
    checks_by_name = {check.name: check for check in checks}
    assert checks_by_name["database_migration_heads"].status == "pass"
    assert checks_by_name["portfolio_transaction_revision_ledger_objects"].status == "fail"
    assert checks_by_name["portfolio_transaction_0037_database_authority"].status == "skip"
    assert checks_by_name["database_schema_dependent_checks"].status == "skip"


def test_missing_0037_database_authority_returns_fail_and_skip() -> None:
    checks, ready = audit._schema_capability_gate(
        _SchemaGateCursor(authority_violations=1)
    )

    assert ready is False
    checks_by_name = {check.name: check for check in checks}
    assert checks_by_name["portfolio_transaction_revision_ledger_objects"].status == "pass"
    assert checks_by_name["portfolio_transaction_0037_database_authority"].status == "fail"
    assert checks_by_name["database_schema_dependent_checks"].status == "skip"


def test_main_serializes_database_query_failures_as_json(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    def raise_missing_table(_database_url: str) -> list[object]:
        raise psycopg.errors.UndefinedTable("missing 0037 ledger object")

    monkeypatch.setattr(audit, "run_audit", raise_missing_table)
    monkeypatch.setattr(sys, "argv", [str(AUDIT_PATH), "--json"])

    assert audit.main() == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed"
    assert payload["failed_count"] == 1
    assert payload["skipped_count"] == 1
    assert payload["checks"][0]["name"] == "database_audit_execution"
    assert payload["checks"][1]["status"] == "skip"
