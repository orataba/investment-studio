from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.read_models import WatchlistRowReadModel
from app.db.models.watchlists import Watchlist
from app.db.session import get_db_session
from app.repositories.sqlalchemy.instrument_attributes import (
    SQLAlchemyInstrumentAttributeRepository,
)
from app.repositories.sqlalchemy.read_models import SQLAlchemyReadModelRepository
from app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
from app.services.read_models import build_latest_quote_overrides


router = APIRouter()
attribute_repository = SQLAlchemyInstrumentAttributeRepository()
read_model_repository = SQLAlchemyReadModelRepository()
recalc_repository = SQLAlchemyRecalcJobRepository()

MONITORING_REQUIRED_FUND_ATTRIBUTE_KEYS = [
    "fund_regime",
    "fund_vehicle",
    "strategy_family",
    "volatility_bucket",
    "style_stability",
    "transparency_quality",
]
FRESHNESS_PRIORITY = {
    "unavailable": 0,
    "pending_recalc": 1,
    "stale": 2,
    "partial": 3,
    "fresh": 4,
}
OPEN_RECALC_JOB_STATUSES = {"queued", "running", "failed"}


def _serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _serialize_date(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_iso_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _value_missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return not any(str(item or "").strip() for item in value)
    return False


def _freshness_priority(status: str | None) -> int:
    return FRESHNESS_PRIORITY.get(str(status or "").strip().lower(), 99)


def _row_latest_activity(record: WatchlistRowReadModel) -> datetime | None:
    candidates = [
        record.last_successful_snapshot_at,
        record.last_recalculated_at,
        record.last_fact_update_at,
    ]
    valid = [value for value in candidates if value is not None]
    return max(valid) if valid else None


@router.get("/dashboard")
def get_monitoring_dashboard(
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    watchlists = session.scalars(
        select(Watchlist).order_by(Watchlist.sort_order, Watchlist.name)
    ).all()
    row_records = session.execute(
        select(WatchlistRowReadModel, Watchlist.name)
        .join(Watchlist, Watchlist.watchlist_id == WatchlistRowReadModel.watchlist_id)
        .order_by(Watchlist.sort_order, Watchlist.name, WatchlistRowReadModel.asset_name)
    ).all()

    asset_ids = list({record.asset_id for record, _ in row_records})
    chart_overrides = build_latest_quote_overrides(
        read_model_repository.list_charts(session, asset_ids)
    )
    attribute_labels = {
        record.attribute_key: record.label
        for record in attribute_repository.list_definitions(session)
    }

    watchlist_summaries: dict[str, dict[str, object]] = {
        record.watchlist_id: {
            "watchlist_id": record.watchlist_id,
            "name": record.name,
            "item_count": 0,
            "needs_refresh_count": 0,
            "missing_quote_count": 0,
            "missing_tag_count": 0,
            "open_recalc_job_count": 0,
            "last_activity_at": None,
        }
        for record in watchlists
    }
    asset_summaries: dict[str, dict[str, object]] = {}

    for row_record, watchlist_name in row_records:
        latest_quote_date = chart_overrides.get(row_record.asset_id, {}).get(
            "latest_quote_date"
        )
        if not isinstance(latest_quote_date, str) or not latest_quote_date:
            latest_quote_date = _serialize_date(row_record.last_nav_date)

        row_activity = _row_latest_activity(row_record)
        watchlist_summary = watchlist_summaries[row_record.watchlist_id]
        watchlist_summary["item_count"] = int(watchlist_summary["item_count"]) + 1
        if (
            row_activity is not None
            and (
                watchlist_summary["last_activity_at"] is None
                or row_activity > watchlist_summary["last_activity_at"]
            )
        ):
            watchlist_summary["last_activity_at"] = row_activity

        asset_summary = asset_summaries.get(row_record.asset_id)
        if asset_summary is None:
            asset_summary = {
                "asset_id": row_record.asset_id,
                "asset_name": row_record.asset_name,
                "asset_type": row_record.asset_type,
                "ticker_or_isin": row_record.ticker_or_isin,
                "management_firm_name": row_record.management_firm_name,
                "data_freshness_status": row_record.data_freshness_status,
                "latest_quote_date": latest_quote_date,
                "last_recalculated_at": row_record.last_recalculated_at,
                "last_activity_at": row_activity,
                "staleness_reason": row_record.staleness_reason,
                "attributes": row_record.attributes_json or {},
                "primary_watchlist_id": row_record.watchlist_id,
                "primary_watchlist_name": watchlist_name,
                "watchlists": [],
            }
            asset_summaries[row_record.asset_id] = asset_summary
        elif _freshness_priority(row_record.data_freshness_status) < _freshness_priority(
            str(asset_summary["data_freshness_status"])
        ):
            asset_summary["data_freshness_status"] = row_record.data_freshness_status
            asset_summary["staleness_reason"] = row_record.staleness_reason

        if asset_summary["latest_quote_date"] is None and latest_quote_date is not None:
            asset_summary["latest_quote_date"] = latest_quote_date
        if (
            row_record.last_recalculated_at is not None
            and (
                asset_summary["last_recalculated_at"] is None
                or row_record.last_recalculated_at > asset_summary["last_recalculated_at"]
            )
        ):
            asset_summary["last_recalculated_at"] = row_record.last_recalculated_at
        if (
            row_activity is not None
            and (
                asset_summary["last_activity_at"] is None
                or row_activity > asset_summary["last_activity_at"]
            )
        ):
            asset_summary["last_activity_at"] = row_activity

        memberships: list[dict[str, object]] = asset_summary["watchlists"]
        if not any(
            item["watchlist_id"] == row_record.watchlist_id for item in memberships
        ):
            memberships.append(
                {"watchlist_id": row_record.watchlist_id, "name": watchlist_name}
            )

    needs_attention_assets: list[dict[str, object]] = []
    missing_tag_assets: list[dict[str, object]] = []

    for asset_summary in asset_summaries.values():
        memberships = sorted(
            asset_summary["watchlists"], key=lambda item: str(item["name"]).lower()
        )
        asset_summary["watchlists"] = memberships
        asset_summary["watchlist_count"] = len(memberships)

        needs_refresh = str(asset_summary["data_freshness_status"]) != "fresh"
        missing_quote = asset_summary["latest_quote_date"] is None

        missing_attribute_keys: list[str] = []
        if str(asset_summary["asset_type"]) == "fund":
            attributes = (
                asset_summary["attributes"]
                if isinstance(asset_summary["attributes"], dict)
                else {}
            )
            missing_attribute_keys = [
                key
                for key in MONITORING_REQUIRED_FUND_ATTRIBUTE_KEYS
                if _value_missing(attributes.get(key))
            ]

        missing_attribute_labels = [
            attribute_labels.get(key, key) for key in missing_attribute_keys
        ]
        asset_summary["missing_attribute_keys"] = missing_attribute_keys
        asset_summary["missing_attribute_labels"] = missing_attribute_labels
        asset_summary["missing_attribute_count"] = len(missing_attribute_keys)
        asset_summary["last_recalculated_at"] = _serialize_datetime(
            asset_summary["last_recalculated_at"]
        )
        asset_summary["last_activity_at"] = _serialize_datetime(
            asset_summary["last_activity_at"]
        )

        issue_flags: list[str] = []
        if needs_refresh:
            issue_flags.append("needs_refresh")
        if missing_quote:
            issue_flags.append("missing_quote")
        asset_summary["issue_flags"] = issue_flags
        asset_summary.pop("attributes", None)

        for membership in memberships:
            watchlist_summary = watchlist_summaries[str(membership["watchlist_id"])]
            if needs_refresh:
                watchlist_summary["needs_refresh_count"] = (
                    int(watchlist_summary["needs_refresh_count"]) + 1
                )
            if missing_quote:
                watchlist_summary["missing_quote_count"] = (
                    int(watchlist_summary["missing_quote_count"]) + 1
                )
            if missing_attribute_keys:
                watchlist_summary["missing_tag_count"] = (
                    int(watchlist_summary["missing_tag_count"]) + 1
                )

        if issue_flags:
            needs_attention_assets.append(asset_summary)
        if missing_attribute_keys:
            missing_tag_assets.append(asset_summary)

    recent_jobs = recalc_repository.list_recent(session, limit=200)
    open_recalc_jobs: list[dict[str, object]] = []
    for job in recent_jobs:
        if job.job_status not in OPEN_RECALC_JOB_STATUSES:
            continue
        asset_summary = asset_summaries.get(job.asset_id)
        if asset_summary is None:
            continue
        memberships = list(asset_summary["watchlists"])
        for membership in memberships:
            watchlist_summary = watchlist_summaries[str(membership["watchlist_id"])]
            watchlist_summary["open_recalc_job_count"] = (
                int(watchlist_summary["open_recalc_job_count"]) + 1
            )
        open_recalc_jobs.append(
            {
                "recalc_job_id": job.recalc_job_id,
                "asset_id": job.asset_id,
                "asset_name": str(asset_summary["asset_name"]),
                "job_type": job.job_type,
                "job_status": job.job_status,
                "trigger_type": job.trigger_type,
                "enqueued_at": _serialize_datetime(job.enqueued_at),
                "started_at": _serialize_datetime(job.started_at),
                "finished_at": _serialize_datetime(job.finished_at),
                "error_message": job.error_message,
                "primary_watchlist_id": asset_summary["primary_watchlist_id"],
                "primary_watchlist_name": asset_summary["primary_watchlist_name"],
                "watchlists": memberships,
            }
        )

    needs_attention_assets.sort(
        key=lambda item: (
            "missing_quote" not in item["issue_flags"],
            _freshness_priority(str(item["data_freshness_status"])),
            item["latest_quote_date"] or "",
            str(item["asset_name"]).lower(),
        )
    )
    missing_tag_assets.sort(
        key=lambda item: (
            -int(item["missing_attribute_count"]),
            str(item["asset_name"]).lower(),
        )
    )
    open_recalc_jobs.sort(
        key=lambda item: (
            0
            if item["job_status"] == "failed"
            else 1
            if item["job_status"] == "running"
            else 2,
            -(
                _parse_iso_datetime(item["enqueued_at"]).timestamp()
                if _parse_iso_datetime(item["enqueued_at"]) is not None
                else 0
            ),
        )
    )

    ordered_watchlists = []
    for watchlist in watchlists:
        summary = watchlist_summaries[watchlist.watchlist_id]
        ordered_watchlists.append(
            {
                **summary,
                "last_activity_at": _serialize_datetime(summary["last_activity_at"]),
            }
        )

    return {
        "generated_at": _serialize_datetime(datetime.now(UTC).replace(microsecond=0)),
        "overview": {
            "watchlist_count": len(watchlists),
            "unique_asset_count": len(asset_summaries),
            "needs_refresh_count": len(
                [
                    item
                    for item in asset_summaries.values()
                    if "needs_refresh" in item["issue_flags"]
                ]
            ),
            "missing_quote_count": len(
                [
                    item
                    for item in asset_summaries.values()
                    if "missing_quote" in item["issue_flags"]
                ]
            ),
            "missing_tag_count": len(missing_tag_assets),
            "open_recalc_job_count": len(open_recalc_jobs),
            "failed_recalc_job_count": len(
                [item for item in open_recalc_jobs if item["job_status"] == "failed"]
            ),
        },
        "watchlists": ordered_watchlists,
        "needs_attention_assets": needs_attention_assets,
        "missing_tag_assets": missing_tag_assets,
        "open_recalc_jobs": open_recalc_jobs,
    }
