from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.presenters import present_field_category, present_field_registry
from app.db.session import get_db_session
from app.repositories.sqlalchemy.field_registry import SQLAlchemyFieldRegistryRepository


router = APIRouter()
field_registry_repository = SQLAlchemyFieldRegistryRepository()


@router.get("")
def get_field_registry(
    category: str | None = Query(default=None),
    asset_type: str | None = Query(default=None),
    product_type: str | None = Query(default=None),
    search: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    fields = [present_field_registry(item) for item in field_registry_repository.list_fields(session)]
    categories = [present_field_category(item) for item in field_registry_repository.list_categories(session)]
    if category:
        fields = [item for item in fields if item["category_code"] == category]
    if asset_type:
        normalized_asset_types = {
            value.strip().lower() for value in asset_type.split(",") if value.strip()
        }
        if normalized_asset_types:
            fields = [
                item
                for item in fields
                if not item["asset_scope_json"]
                or bool(normalized_asset_types.intersection(item["asset_scope_json"]))
            ]
    if product_type:
        fields = [
            item
            for item in fields
            if not item["product_scope_json"] or product_type in item["product_scope_json"]
        ]
    if search:
        needle = search.lower()
        fields = [
            item
            for item in fields
            if needle in item["label"].lower() or needle in item["field_key"].lower()
        ]
    return {
        "categories": categories,
        "fields": fields,
        "total_fields": len(fields),
    }
