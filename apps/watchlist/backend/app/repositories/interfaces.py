from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from sqlalchemy.orm import Session

from app.db.models.assets import AssetDetail
from app.db.models.recalc import RecalcJob
from app.db.models.watchlists import (
    FieldCategory,
    FieldRegistry,
    InstrumentAttributeDefinition,
    InstrumentAttributeValue,
    Watchlist,
    WatchlistItem,
    WatchlistView,
)


class WatchlistRepository(Protocol):
    def list(self, session: Session) -> Sequence[Watchlist]: ...

    def get(self, session: Session, watchlist_id: str) -> Watchlist | None: ...

    def list_views(self, session: Session, watchlist_id: str) -> Sequence[WatchlistView]: ...

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
    ) -> Watchlist: ...

    def add_items(
        self,
        session: Session,
        *,
        watchlist_id: str,
        asset_ids: list[str],
        added_by: str,
    ) -> Sequence[WatchlistItem]: ...

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
    ) -> WatchlistView: ...


class FieldRegistryRepository(Protocol):
    def list_categories(self, session: Session) -> Sequence[FieldCategory]: ...

    def list_fields(self, session: Session) -> Sequence[FieldRegistry]: ...

    def get(self, session: Session, field_key: str) -> FieldRegistry | None: ...

    def create(
        self,
        session: Session,
        *,
        field_key: str,
        label: str,
        description: str | None,
        category_code: str,
        data_type: str,
        formatter_code: str,
        sort_mode: str,
        filter_mode: str,
        group_mode: str,
        asset_scope_json: list[str],
        product_scope_json: list[str],
        availability_rule_json: dict[str, object],
        source_domain: str,
        source_metric_code: str,
        default_width: int | None,
        default_visible: bool,
    ) -> FieldRegistry: ...


class InstrumentAttributeRepository(Protocol):
    def list_definitions(
        self,
        session: Session,
    ) -> Sequence[InstrumentAttributeDefinition]: ...

    def create_definition(
        self,
        session: Session,
        *,
        attribute_key: str,
        label: str,
        description: str | None,
        data_type: str,
        options_json: list[str],
        is_groupable: bool,
        is_filterable: bool,
        is_view_column: bool,
        default_visible: bool,
    ) -> InstrumentAttributeDefinition: ...

    def get_values_for_asset(
        self,
        session: Session,
        asset_id: str,
    ) -> Sequence[InstrumentAttributeValue]: ...

    def add_value(
        self,
        session: Session,
        *,
        asset_id: str,
        attribute_key: str,
        value_json: object,
        source_record_id: str | None,
    ) -> InstrumentAttributeValue: ...


class RecalcJobRepository(Protocol):
    def list_recent(self, session: Session, limit: int = 100) -> Sequence[RecalcJob]: ...

    def get(self, session: Session, job_id: str) -> RecalcJob | None: ...

    def create(
        self,
        session: Session,
        *,
        recalc_job_id: str,
        job_type: str,
        asset_id: str,
        trigger_type: str,
        trigger_ref_type: str | None,
        trigger_ref_id: str | None,
        job_status: str,
        priority: int,
        dedupe_key: str,
        payload_json: dict[str, object],
    ) -> RecalcJob: ...


class AssetRepository(Protocol):
    def get(self, session: Session, asset_id: str) -> AssetDetail | None: ...
