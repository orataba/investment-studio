from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from watchlist_app.db.models.watchlists import (
    Watchlist,
    WatchlistItem,
    WatchlistView,
    WatchlistViewColumn,
)


def _scoped_view_id(watchlist_id: str, local_view_id: str) -> str:
    return f"{watchlist_id}::{local_view_id}"


def _slugify_local_view_id(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    slug = slug.strip("-")
    return slug or "view"


def _allocate_local_view_id(
    used_local_view_ids: set[str],
    preferred_local_view_id: str,
) -> str:
    base = _slugify_local_view_id(preferred_local_view_id)
    candidate = base
    suffix = 2
    while candidate in used_local_view_ids:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used_local_view_ids.add(candidate)
    return candidate


FUND_SCREENING_VIEW_ID = "fund-screening"
FUND_SCREENING_VIEW_NAME = "产品分类筛选"
FUND_SCREENING_VIEW_DESCRIPTION = "先按分类树缩小产品池，再叠加研究标签和监控判断。"
LOCAL_DETAIL_VIEW_FILTERS = {"instrument_type": ["fund", "index"]}
ALL_COVERAGE_WATCHLIST_ID = "all-coverage"
ALL_COVERAGE_WATCHLIST_NAME = "All Covered"
ALL_COVERAGE_WATCHLIST_DESCRIPTION = (
    "System-maintained coverage of every active fund and index in the shared instrument registry."
)
SYSTEM_OWNER_TYPE = "system"
SYSTEM_OWNER_ID = "watchlist"
TAXONOMY_GROUP_BY_CODE = "taxonomy"


def _local_detail_view_filters() -> dict[str, list[str]]:
    return {"instrument_type": list(LOCAL_DETAIL_VIEW_FILTERS["instrument_type"])}


def _overview_view_columns() -> list[dict[str, object]]:
    return [
        {"field_key": "instrument_name", "display_order": 1, "width": 320},
        {"field_key": "attr.coverage_status", "display_order": 2, "width": 110},
        {"field_key": "price_chart_1m", "display_order": 3, "width": 140},
        {"field_key": "latest_quote", "display_order": 4, "width": 130},
        {"field_key": "latest_quote_date", "display_order": 5, "width": 140},
        {"field_key": "return_1w", "display_order": 6, "width": 150},
        {"field_key": "return_mtd", "display_order": 7, "width": 120},
        {"field_key": "return_ytd", "display_order": 8, "width": 150},
        {"field_key": "attr.current_drawdown", "display_order": 9, "width": 120},
    ]


def _fund_screening_view_columns() -> list[dict[str, object]]:
    return [
        {"field_key": "instrument_name", "display_order": 1, "width": 320},
        {"field_key": "attr.implementation_style", "display_order": 2, "width": 140},
        {"field_key": "attr.style_profile", "display_order": 3, "width": 220},
        {"field_key": "attr.manager_assessment", "display_order": 4, "width": 220},
        {"field_key": "attr.volatility_bucket", "display_order": 5, "width": 120},
        {"field_key": "attr.drawdown_control", "display_order": 6, "width": 120},
        {"field_key": "attr.style_stability", "display_order": 7, "width": 120},
        {"field_key": "attr.transparency_quality", "display_order": 8, "width": 120},
        {"field_key": "data_freshness_status", "display_order": 9, "width": 140},
    ]


class SQLAlchemyWatchlistRepository:
    def list(self, session: Session) -> Sequence[Watchlist]:
        stmt = (
            select(Watchlist)
            .options(selectinload(Watchlist.items), selectinload(Watchlist.views))
            .order_by(Watchlist.sort_order, Watchlist.name)
        )
        return session.scalars(stmt).all()

    def get(self, session: Session, watchlist_id: str) -> Watchlist | None:
        stmt = (
            select(Watchlist)
            .options(selectinload(Watchlist.items), selectinload(Watchlist.views))
            .where(Watchlist.watchlist_id == watchlist_id)
        )
        return session.scalars(stmt).first()

    def list_views(self, session: Session, watchlist_id: str) -> Sequence[WatchlistView]:
        stmt = (
            select(WatchlistView)
            .options(selectinload(WatchlistView.columns))
            .where(WatchlistView.watchlist_id == watchlist_id)
            .order_by(WatchlistView.is_default.desc(), WatchlistView.name)
        )
        return session.scalars(stmt).all()

    def get_view(
        self,
        session: Session,
        *,
        watchlist_id: str,
        view_id: str,
    ) -> WatchlistView | None:
        return session.get(WatchlistView, _scoped_view_id(watchlist_id, view_id))

    def create(
        self,
        session: Session,
        *,
        watchlist_id: str,
        name: str,
        description: str | None,
        owner_type: str,
        owner_id: str,
        default_view_id: str,
        is_default: bool = False,
        is_shared: bool = False,
        sort_order: int | None = None,
        overview_default_group_by: str | None = "none",
        fund_screening_default_group_by: str | None = "attr.fund_taxonomy_level_1",
    ) -> Watchlist:
        next_sort_order = self._next_sort_order(session) if sort_order is None else sort_order
        record = Watchlist(
            watchlist_id=watchlist_id,
            name=name,
            description=description,
            owner_type=owner_type,
            owner_id=owner_id,
            is_default=is_default,
            is_shared=is_shared,
            sort_order=next_sort_order,
        )
        session.add(record)
        session.flush()
        self.create_view(
            session,
            watchlist_id=watchlist_id,
            view_id=default_view_id,
            name="Overview",
            description="Default overview view",
            kind="system",
            default_group_by=overview_default_group_by,
            default_sort=[],
            default_filters={},
            default_advanced_filter={},
            columns=_overview_view_columns(),
            is_default=True,
        )
        self.create_view(
            session,
            watchlist_id=watchlist_id,
            view_id=FUND_SCREENING_VIEW_ID,
            name=FUND_SCREENING_VIEW_NAME,
            description=FUND_SCREENING_VIEW_DESCRIPTION,
            kind="system",
            default_group_by=fund_screening_default_group_by,
            default_sort=[],
            default_filters=_local_detail_view_filters(),
            default_advanced_filter={},
            columns=_fund_screening_view_columns(),
            is_default=False,
        )
        session.flush()
        return record

    def ensure_all_coverage_watchlist(self, session: Session) -> Watchlist:
        record = self.get(session, ALL_COVERAGE_WATCHLIST_ID)
        if record is None:
            existing_sort_orders = [
                row[0]
                for row in session.execute(select(Watchlist.sort_order)).all()
                if row[0] is not None
            ]
            sort_order = min(existing_sort_orders) - 1 if existing_sort_orders else 0
            try:
                record = self.create(
                    session,
                    watchlist_id=ALL_COVERAGE_WATCHLIST_ID,
                    name=ALL_COVERAGE_WATCHLIST_NAME,
                    description=ALL_COVERAGE_WATCHLIST_DESCRIPTION,
                    owner_type=SYSTEM_OWNER_TYPE,
                    owner_id=SYSTEM_OWNER_ID,
                    default_view_id="overview",
                    is_default=True,
                    is_shared=True,
                    sort_order=sort_order,
                    overview_default_group_by=TAXONOMY_GROUP_BY_CODE,
                    fund_screening_default_group_by=TAXONOMY_GROUP_BY_CODE,
                )
                session.flush()
                return self.get(session, ALL_COVERAGE_WATCHLIST_ID) or record
            except IntegrityError:
                session.rollback()
                record = self.get(session, ALL_COVERAGE_WATCHLIST_ID)
                if record is None:
                    raise

        record.name = ALL_COVERAGE_WATCHLIST_NAME
        record.description = ALL_COVERAGE_WATCHLIST_DESCRIPTION
        record.owner_type = SYSTEM_OWNER_TYPE
        record.owner_id = SYSTEM_OWNER_ID
        record.is_default = True
        record.is_shared = True

        overview = self.get_view(
            session,
            watchlist_id=ALL_COVERAGE_WATCHLIST_ID,
            view_id="overview",
        )
        if overview is None:
            self.create_view(
                session,
                watchlist_id=ALL_COVERAGE_WATCHLIST_ID,
                view_id="overview",
                name="Overview",
                description="Default overview view",
                kind="system",
                default_group_by=TAXONOMY_GROUP_BY_CODE,
                default_sort=[],
                default_filters={},
                default_advanced_filter={},
                columns=_overview_view_columns(),
                is_default=True,
            )
        else:
            overview.kind = "system"
            overview.default_group_by = TAXONOMY_GROUP_BY_CODE
            overview.default_filters_json = {}
            overview.is_default = True

        screening = self.get_view(
            session,
            watchlist_id=ALL_COVERAGE_WATCHLIST_ID,
            view_id=FUND_SCREENING_VIEW_ID,
        )
        if screening is None:
            self.create_view(
                session,
                watchlist_id=ALL_COVERAGE_WATCHLIST_ID,
                view_id=FUND_SCREENING_VIEW_ID,
                name=FUND_SCREENING_VIEW_NAME,
                description=FUND_SCREENING_VIEW_DESCRIPTION,
                kind="system",
                default_group_by=TAXONOMY_GROUP_BY_CODE,
                default_sort=[],
                default_filters=_local_detail_view_filters(),
                default_advanced_filter={},
                columns=_fund_screening_view_columns(),
                is_default=False,
            )
        else:
            screening.kind = "system"
            screening.name = FUND_SCREENING_VIEW_NAME
            screening.description = FUND_SCREENING_VIEW_DESCRIPTION
            screening.default_group_by = TAXONOMY_GROUP_BY_CODE
            screening.default_filters_json = _local_detail_view_filters()

        default_view_key = _scoped_view_id(ALL_COVERAGE_WATCHLIST_ID, "overview")
        for view in session.scalars(
            select(WatchlistView).where(
                WatchlistView.watchlist_id == ALL_COVERAGE_WATCHLIST_ID
            )
        ).all():
            view.is_default = view.watchlist_view_id == default_view_key

        session.flush()
        return self.get(session, ALL_COVERAGE_WATCHLIST_ID) or record

    def _next_sort_order(self, session: Session) -> int:
        records = session.scalars(select(Watchlist).order_by(Watchlist.sort_order)).all()
        if not records:
            return 0
        return max(item.sort_order for item in records) + 1

    def duplicate(self, session: Session, *, source_watchlist_id: str, watchlist_id: str, name: str) -> Watchlist | None:
        source = (
            session.query(Watchlist)
            .options(
                selectinload(Watchlist.items),
                selectinload(Watchlist.views).selectinload(WatchlistView.columns),
            )
            .filter(Watchlist.watchlist_id == source_watchlist_id)
            .first()
        )
        if source is None:
            return None

        copied = Watchlist(
            watchlist_id=watchlist_id,
            name=name,
            description=source.description,
            owner_type=source.owner_type,
            owner_id=source.owner_id,
            is_default=False,
            is_shared=source.is_shared,
            sort_order=self._next_sort_order(session),
        )
        session.add(copied)
        session.flush()

        now = datetime.now(UTC).replace(microsecond=0)
        for item in source.items:
            session.add(
                WatchlistItem(
                    watchlist_id=watchlist_id,
                    instrument_id=item.instrument_id,
                    added_at=item.added_at,
                    added_by=item.added_by,
                    note=item.note,
                    rank_hint=item.rank_hint,
                )
            )

        used_local_view_ids: set[str] = set()
        for view in source.views:
            local_view_id = (
                view.watchlist_view_id[len(f"{source_watchlist_id}::") :]
                if view.watchlist_view_id.startswith(f"{source_watchlist_id}::")
                else view.watchlist_view_id
            )
            local_view_id = _allocate_local_view_id(used_local_view_ids, local_view_id)
            scoped_view_id = _scoped_view_id(watchlist_id, local_view_id)
            copied_view = WatchlistView(
                watchlist_view_id=scoped_view_id,
                watchlist_id=watchlist_id,
                name=view.name,
                description=view.description,
                kind=view.kind,
                default_sort_json=view.default_sort_json,
                default_filters_json=view.default_filters_json,
                default_advanced_filter_json=view.default_advanced_filter_json,
                default_group_by=view.default_group_by,
                density=view.density,
                is_default=view.is_default,
                created_at=now,
            )
            session.add(copied_view)
            session.flush()
            for column in view.columns:
                session.add(
                    WatchlistViewColumn(
                        watchlist_view_id=scoped_view_id,
                        field_key=column.field_key,
                        display_order=column.display_order,
                        width=column.width,
                        is_visible=column.is_visible,
                        pin_side=column.pin_side,
                    )
                )

        session.flush()
        return copied

    def delete(self, session: Session, *, watchlist_id: str) -> bool:
        record = session.get(Watchlist, watchlist_id)
        if record is None:
            return False
        session.delete(record)
        session.flush()
        return True

    def reorder(self, session: Session, *, watchlist_ids: list[str]) -> Sequence[Watchlist]:
        records = self.list(session)
        by_id = {item.watchlist_id: item for item in records}
        ordered_ids = [watchlist_id for watchlist_id in watchlist_ids if watchlist_id in by_id]
        remaining_ids = [item.watchlist_id for item in records if item.watchlist_id not in ordered_ids]
        final_ids = ordered_ids + remaining_ids

        for index, watchlist_id in enumerate(final_ids):
            by_id[watchlist_id].sort_order = index

        session.flush()
        return self.list(session)

    def add_items(
        self,
        session: Session,
        *,
        watchlist_id: str,
        instrument_ids: list[str],
        added_by: str,
    ) -> Sequence[WatchlistItem]:
        existing = {
            row[0]
            for row in session.execute(
                select(WatchlistItem.instrument_id).where(WatchlistItem.watchlist_id == watchlist_id)
            )
        }
        now = datetime.now(UTC).replace(microsecond=0)
        created: list[WatchlistItem] = []
        for instrument_id in instrument_ids:
            if instrument_id in existing:
                continue
            row = WatchlistItem(
                watchlist_id=watchlist_id,
                instrument_id=instrument_id,
                added_at=now,
                added_by=added_by,
                note=None,
                rank_hint=None,
            )
            session.add(row)
            created.append(row)
        session.flush()
        return created

    def delete_items(
        self,
        session: Session,
        *,
        watchlist_id: str,
        instrument_ids: list[str],
    ) -> int:
        if not instrument_ids:
            return 0
        stmt = select(WatchlistItem).where(
            WatchlistItem.watchlist_id == watchlist_id,
            WatchlistItem.instrument_id.in_(instrument_ids),
        )
        rows = session.scalars(stmt).all()
        for row in rows:
            session.delete(row)
        session.flush()
        return len(rows)

    def create_view(
        self,
        session: Session,
        *,
        watchlist_id: str,
        view_id: str,
        name: str,
        description: str | None,
        kind: str,
        default_group_by: str | None,
        default_sort: list[dict[str, object]],
        default_filters: dict[str, object],
        default_advanced_filter: dict[str, object],
        columns: list[dict[str, object]],
        is_default: bool = False,
    ) -> WatchlistView:
        scoped_view_id = _scoped_view_id(watchlist_id, view_id)
        existing_views = session.scalars(
            select(WatchlistView).where(WatchlistView.watchlist_id == watchlist_id)
        ).all()
        should_be_default = is_default or not existing_views
        if should_be_default:
            for view in existing_views:
                view.is_default = False
        record = WatchlistView(
            watchlist_view_id=scoped_view_id,
            watchlist_id=watchlist_id,
            name=name,
            description=description,
            kind=kind,
            default_group_by=default_group_by,
            default_sort_json=default_sort,
            default_filters_json=default_filters,
            default_advanced_filter_json=default_advanced_filter,
            density="standard",
            is_default=should_be_default,
            created_at=datetime.now(UTC).replace(microsecond=0),
        )
        session.add(record)
        session.flush()
        for item in columns:
            session.add(
                WatchlistViewColumn(
                    watchlist_view_id=scoped_view_id,
                    field_key=str(item["field_key"]),
                    display_order=int(item["display_order"]),
                    width=item.get("width"),
                    is_visible=bool(item.get("is_visible", True)),
                    pin_side=item.get("pin_side"),
                )
            )
        session.flush()
        return record

    def update_view(
        self,
        session: Session,
        *,
        watchlist_id: str,
        view_id: str,
        name: str,
        description: str | None,
        default_group_by: str | None,
        default_sort: list[dict[str, object]],
        default_filters: dict[str, object],
        default_advanced_filter: dict[str, object],
        columns: list[dict[str, object]],
    ) -> WatchlistView:
        scoped_view_id = _scoped_view_id(watchlist_id, view_id)
        record = session.get(WatchlistView, scoped_view_id)
        if record is None:
            raise ValueError("Watchlist view not found")
        record.name = name
        record.description = description
        record.default_group_by = default_group_by
        record.default_sort_json = default_sort
        record.default_filters_json = default_filters
        record.default_advanced_filter_json = default_advanced_filter
        for column in list(record.columns):
            session.delete(column)
        session.flush()
        for item in columns:
            session.add(
                WatchlistViewColumn(
                    watchlist_view_id=scoped_view_id,
                    field_key=str(item["field_key"]),
                    display_order=int(item["display_order"]),
                    width=item.get("width"),
                    is_visible=bool(item.get("is_visible", True)),
                    pin_side=item.get("pin_side"),
                )
            )
        session.flush()
        return record
