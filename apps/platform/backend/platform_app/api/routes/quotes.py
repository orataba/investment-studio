from __future__ import annotations

from fastapi import APIRouter, HTTPException

from portfolio_ops_instrument_core.quote_resolver import QuoteResolverError

from platform_app.api.contracts import (
    PlatformCanonicalQuoteResolution,
    PlatformExplicitQuoteResolveRequest,
    PlatformRoleQuoteResolveRequest,
)
from platform_app.services.quote_resolver import (
    resolve_explicit_quote,
    resolve_role_quote,
)


router = APIRouter()


def _resolver_error(error: QuoteResolverError) -> HTTPException:
    return HTTPException(
        status_code=404 if error.reason_code == "instrument_not_found" else 422,
        detail={"reason_code": error.reason_code, "message": str(error)},
    )


@router.post(
    "/resolve-explicit",
    response_model=PlatformCanonicalQuoteResolution,
)
def resolve_explicit_quote_request(
    payload: PlatformExplicitQuoteResolveRequest,
) -> PlatformCanonicalQuoteResolution:
    try:
        resolution = resolve_explicit_quote(**payload.model_dump())
    except QuoteResolverError as error:
        raise _resolver_error(error) from error
    return PlatformCanonicalQuoteResolution.model_validate(resolution.model_dump())


@router.post(
    "/resolve-role",
    response_model=PlatformCanonicalQuoteResolution,
)
def resolve_role_quote_request(
    payload: PlatformRoleQuoteResolveRequest,
) -> PlatformCanonicalQuoteResolution:
    try:
        resolution = resolve_role_quote(**payload.model_dump())
    except QuoteResolverError as error:
        raise _resolver_error(error) from error
    return PlatformCanonicalQuoteResolution.model_validate(resolution.model_dump())
