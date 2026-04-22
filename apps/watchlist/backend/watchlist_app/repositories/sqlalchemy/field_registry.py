from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from watchlist_app.db.models.watchlists import FieldCategory, FieldRegistry


class SQLAlchemyFieldRegistryRepository:
    def list_categories(self, session: Session) -> Sequence[FieldCategory]:
        stmt = select(FieldCategory).order_by(FieldCategory.display_order, FieldCategory.label)
        return session.scalars(stmt).all()

    def list_fields(self, session: Session) -> Sequence[FieldRegistry]:
        stmt = select(FieldRegistry).order_by(FieldRegistry.category_code, FieldRegistry.label)
        return session.scalars(stmt).all()

    def get(self, session: Session, field_key: str) -> FieldRegistry | None:
        return session.get(FieldRegistry, field_key)

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
    ) -> FieldRegistry:
        record = FieldRegistry(
            field_key=field_key,
            label=label,
            description=description,
            category_code=category_code,
            data_type=data_type,
            formatter_code=formatter_code,
            sort_mode=sort_mode,
            filter_mode=filter_mode,
            group_mode=group_mode,
            asset_scope_json=asset_scope_json,
            product_scope_json=product_scope_json,
            availability_rule_json=availability_rule_json,
            source_domain=source_domain,
            source_metric_code=source_metric_code,
            default_width=default_width,
            default_visible=default_visible,
        )
        session.add(record)
        session.flush()
        return record
