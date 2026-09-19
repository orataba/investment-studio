"""Directory discovery and explicit registration through the shared data owner."""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from studio_identity import current_principal
from studio_runtime import operation
from watchlist_app.db.session import get_session_factory

from investment_studio_instrument_core.security_catalog import (
    SecurityCatalogError, SecurityMaterializeRequest, SecuritySearchResponse,
    materialize_catalog_security, search_catalog,
)
from watchlist_app.services.research_access import require_team_write


router = APIRouter()


class RegisteredSecurity(BaseModel):
    instrument_id: str
    instrument_name: str
    instrument_type: str
    currency: str
    identifiers: list[dict[str, object]]
    exchange_code: str | None = None


@router.get("/search", response_model=SecuritySearchResponse)
def search_securities(q: str = Query(min_length=1, max_length=200), limit: int = Query(default=12, ge=1, le=25)):
    if not q.strip():
        raise HTTPException(422, "请输入证券代码或名称")
    try:
        with operation("security_catalog_search"):
            return search_catalog(q.strip(), limit, session_factory=get_session_factory())
    except SecurityCatalogError as error:
        raise HTTPException(error.status_code, str(error)) from error


@router.post("/materialize", response_model=RegisteredSecurity)
def materialize_security(payload: SecurityMaterializeRequest):
    principal = require_team_write()
    # Registration is a person's explicit selection, never a delegated research
    # action. The data-owner command validates identity and refreshes prices.
    if principal.kind != "user" or current_principal().resource_scope:
        raise HTTPException(403, "证券登记需要团队成员明确选择")
    try:
        return RegisteredSecurity.model_validate(materialize_catalog_security(payload))
    except SecurityCatalogError as error:
        raise HTTPException(error.status_code, str(error)) from error
    except (KeyError, ValueError) as error:
        raise HTTPException(502, "证券登记入口返回的数据格式无效。") from error
