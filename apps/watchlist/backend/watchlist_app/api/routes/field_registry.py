from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from watchlist_app.api.presenters import present_field_category, present_field_registry
from watchlist_app.db.session import get_db_session
from watchlist_app.repositories.sqlalchemy.field_registry import SQLAlchemyFieldRegistryRepository


router = APIRouter()
field_registry_repository = SQLAlchemyFieldRegistryRepository()


@router.get("")
def get_field_registry(
    category: str | None = Query(default=None),
    instrument_type: str | None = Query(default=None),
    product_type: str | None = Query(default=None),
    search: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    fields = [present_field_registry(item) for item in field_registry_repository.list_fields(session)]
    categories = [present_field_category(item) for item in field_registry_repository.list_categories(session)]
    if category:
        fields = [item for item in fields if item["category_code"] == category]
    if instrument_type:
        normalized_instrument_types = {
            value.strip().lower() for value in instrument_type.split(",") if value.strip()
        }
        if normalized_instrument_types:
            fields = [
                item
                for item in fields
                if not item["instrument_scope_json"]
                or normalized_instrument_types.issubset(
                    {
                        str(value).strip().lower()
                        for value in item["instrument_scope_json"]
                        if str(value).strip()
                    }
                )
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
