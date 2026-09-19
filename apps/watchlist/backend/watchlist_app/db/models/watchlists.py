from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from watchlist_app.db.base import Base
from watchlist_app.db.models.common import TimestampMixin


class Watchlist(TimestampMixin, Base):
    __tablename__ = "watchlist"

    watchlist_id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(nullable=False)
    description: Mapped[str | None]
    owner_type: Mapped[str] = mapped_column(nullable=False)
    owner_id: Mapped[str] = mapped_column(nullable=False)
    created_by_user_id: Mapped[str | None]
    created_by_display_name: Mapped[str | None]
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    items: Mapped[list["WatchlistItem"]] = relationship(
        back_populates="watchlist",
        cascade="all, delete-orphan",
    )
    views: Mapped[list["WatchlistView"]] = relationship(
        back_populates="watchlist",
        cascade="all, delete-orphan",
    )


class WatchlistItem(Base):
    __tablename__ = "watchlist_item"
    __table_args__ = (
        UniqueConstraint(
            "watchlist_id",
            "instrument_id",
            name="uq_watchlist_item_watchlist_instrument",
        ),
        Index("idx_watchlist_item_watchlist_id", "watchlist_id"),
    )

    watchlist_item_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    watchlist_id: Mapped[str] = mapped_column(
        ForeignKey("watchlist.watchlist_id", ondelete="CASCADE"),
        nullable=False,
    )
    instrument_id: Mapped[str] = mapped_column(nullable=False)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    added_by: Mapped[str | None]
    note: Mapped[str | None]
    rank_hint: Mapped[int | None]

    watchlist: Mapped[Watchlist] = relationship(back_populates="items")


class WatchlistUserSettings(Base):
    __tablename__ = "watchlist_user_settings"
    user_id: Mapped[str] = mapped_column(primary_key=True)
    ordered_watchlist_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)


class WatchlistView(Base):
    __tablename__ = "watchlist_view"

    watchlist_view_id: Mapped[str] = mapped_column(primary_key=True)
    author_user_id: Mapped[str | None]
    base_view_id: Mapped[str | None]
    watchlist_id: Mapped[str] = mapped_column(
        ForeignKey("watchlist.watchlist_id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(nullable=False)
    description: Mapped[str | None]
    kind: Mapped[str] = mapped_column(nullable=False)
    default_sort_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    default_filters_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    default_advanced_filter_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    default_group_by: Mapped[str | None]
    density: Mapped[str | None]
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    watchlist: Mapped[Watchlist] = relationship(back_populates="views")
    columns: Mapped[list["WatchlistViewColumn"]] = relationship(
        back_populates="view",
        cascade="all, delete-orphan",
    )


class WatchlistViewColumn(Base):
    __tablename__ = "watchlist_view_column"
    __table_args__ = (
        UniqueConstraint(
            "watchlist_view_id",
            "field_key",
            name="uq_watchlist_view_column_view_field",
        ),
    )

    watchlist_view_column_id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )
    watchlist_view_id: Mapped[str] = mapped_column(
        ForeignKey("watchlist_view.watchlist_view_id", ondelete="CASCADE"),
        nullable=False,
    )
    field_key: Mapped[str] = mapped_column(nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int | None]
    is_visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    pin_side: Mapped[str | None]

    view: Mapped[WatchlistView] = relationship(back_populates="columns")


class FieldCategory(Base):
    __tablename__ = "field_category"

    category_code: Mapped[str] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(nullable=False)
    parent_category_code: Mapped[str | None] = mapped_column(
        ForeignKey("field_category.category_code")
    )
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class FieldRegistry(Base):
    __tablename__ = "field_registry"

    field_key: Mapped[str] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(nullable=False)
    description: Mapped[str | None]
    category_code: Mapped[str] = mapped_column(
        ForeignKey("field_category.category_code"),
        nullable=False,
    )
    data_type: Mapped[str] = mapped_column(nullable=False)
    formatter_code: Mapped[str] = mapped_column(nullable=False)
    sort_mode: Mapped[str] = mapped_column(nullable=False)
    filter_mode: Mapped[str] = mapped_column(nullable=False)
    group_mode: Mapped[str] = mapped_column(nullable=False)
    instrument_scope_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    product_scope_json: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    availability_rule_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    source_domain: Mapped[str] = mapped_column(nullable=False)
    source_metric_code: Mapped[str] = mapped_column(nullable=False)
    default_width: Mapped[int | None]
    default_visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class InstrumentAttributeDefinition(Base):
    __tablename__ = "instrument_attribute_definition"

    attribute_key: Mapped[str] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(nullable=False)
    description: Mapped[str | None]
    data_type: Mapped[str] = mapped_column(nullable=False)
    domain_code: Mapped[str] = mapped_column(nullable=False)
    group_code: Mapped[str] = mapped_column(nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    options_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    instrument_scope_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    applicability_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    rubric_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    is_groupable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_filterable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_view_column: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    default_visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    required_for_monitoring: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    values: Mapped[list["InstrumentAttributeValue"]] = relationship(
        back_populates="definition",
        cascade="all, delete-orphan",
    )


class InstrumentAttributeValue(Base):
    __tablename__ = "instrument_attribute_value"
    __table_args__ = (
        Index(
            "idx_instrument_attribute_value_instrument_attribute",
            "instrument_id",
            "attribute_key",
            "adopted_at",
        ),
    )

    instrument_attribute_value_id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument_detail.instrument_id", ondelete="CASCADE"),
        nullable=False,
    )
    attribute_key: Mapped[str] = mapped_column(
        ForeignKey(
            "instrument_attribute_definition.attribute_key",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    value_json: Mapped[Any] = mapped_column(JSON, nullable=False)
    effective_from: Mapped[date | None] = mapped_column(Date)
    adopted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_record_id: Mapped[str | None]

    definition: Mapped[InstrumentAttributeDefinition] = relationship(
        back_populates="values"
    )


class InstrumentTaxonomyNode(Base):
    __tablename__ = "instrument_taxonomy_node"
    __table_args__ = (
        Index(
            "idx_instrument_taxonomy_node_taxonomy_parent_order",
            "taxonomy_code",
            "parent_node_id",
            "display_order",
        ),
    )

    node_id: Mapped[str] = mapped_column(primary_key=True)
    taxonomy_code: Mapped[str] = mapped_column(String, nullable=False)
    instrument_type: Mapped[str] = mapped_column(String, nullable=False)
    label: Mapped[str] = mapped_column(String, nullable=False)
    parent_node_id: Mapped[str | None] = mapped_column(
        ForeignKey("instrument_taxonomy_node.node_id", ondelete="CASCADE")
    )
    level_index: Mapped[int] = mapped_column(Integer, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_leaf: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    path_labels_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    path_node_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)


class InstrumentTaxonomyAssignment(Base):
    __tablename__ = "instrument_taxonomy_assignment"
    __table_args__ = (
        Index(
            "idx_instrument_taxonomy_assignment_taxonomy_node",
            "taxonomy_code",
            "node_id",
        ),
    )

    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument_detail.instrument_id", ondelete="CASCADE"),
        primary_key=True,
    )
    taxonomy_code: Mapped[str] = mapped_column(String, primary_key=True)
    node_id: Mapped[str | None] = mapped_column(
        ForeignKey("instrument_taxonomy_node.node_id", ondelete="CASCADE")
    )
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_record_id: Mapped[str | None]


class InstrumentTaxonomyAssignmentHistory(Base):
    __tablename__ = "instrument_taxonomy_assignment_history"
    __table_args__ = (
        Index(
            "idx_instrument_taxonomy_assignment_history_instrument_time",
            "instrument_id",
            "taxonomy_code",
            "assigned_at",
        ),
    )

    history_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument_detail.instrument_id", ondelete="CASCADE"),
        nullable=False,
    )
    taxonomy_code: Mapped[str] = mapped_column(String, nullable=False)
    node_id: Mapped[str | None] = mapped_column(String)
    path_labels_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_record_id: Mapped[str | None]
