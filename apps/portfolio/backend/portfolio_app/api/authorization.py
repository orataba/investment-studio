"""Authorize every API path before its handler runs, including indirect resources."""
from fastapi import HTTPException, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from studio_identity import IdentityError, principal_context, resolve_request
from studio_runtime import operation

from portfolio_app.services.portfolio_access import require_access
from portfolio_app.core.settings import get_settings


async def authenticated_principal(request: Request):
    """Dedicated dependency seam: tests may replace identity, never production ACLs."""
    try:
        with operation("identity"):
            return await run_in_threadpool(resolve_request, request, audience="portfolio", allowed_origins=get_settings().cors_origins)
    except IdentityError as error:
        raise HTTPException(error.status_code, error.detail) from error


def _capture_scope(request: Request, principal, portfolio_id: str) -> None:
    scope = principal.resource_scope or {}
    if scope.get("kind") != "capture":
        return
    from portfolio_app.services.transaction_captures import get_transaction_capture_batch
    batch = get_transaction_capture_batch(portfolio_id=portfolio_id, batch_id=str(scope["id"]))
    if not batch:
        raise HTTPException(404, "截图任务不存在或无权访问")
    route = request.url.path
    params = request.path_params
    if params.get("batch_id") is not None and params["batch_id"] != scope["id"]:
        raise HTTPException(403, "工具仅可访问当前截图任务")
    allowed = request.method == "GET" and (
        route.endswith(("/agent-context", "/instruments", "/transactions", "/access"))
        or route == "/api/workspace/holdings"
    )
    if request.method == "GET" and params.get("capture_id") is not None and route.endswith("/image"):
        allowed = any(item["capture_id"] == params.get("capture_id") for item in batch.get("captures", []))
    if request.method == "POST" and route.endswith(("/transaction-imports/preview", "/analysis-revisions")):
        allowed = True
    if not allowed:
        raise HTTPException(403, "截图助手只能读取本任务材料并提交待人工复核的草稿")


async def authorize(request: Request, principal) -> None:
    path = request.url.path
    method = request.method
    scope = principal.resource_scope or {}
    portfolio_id = request.path_params.get("portfolio_id") or request.query_params.get("portfolio_id")
    if path.startswith("/api/research-assistant/"):
        # A personal conversation is available to portfolio viewers. Watchlist
        # checks the original user's topic ownership and bound portfolio access.
        if scope or principal.kind != "user":
            raise HTTPException(403, "研究助手个人对话仅供已登录用户使用")
        return
    if path.startswith("/api/instrument-risk") and method not in {"GET", "HEAD"}:
        payload = await request.json()
        portfolio_id = payload.get("portfolio_id") if isinstance(payload, dict) else None
    # Portfolio delegations support research reads; local owner authority does
    # not let a model turn that delegation into a ledger or permission writer.
    if scope.get("kind") == "portfolio" and method not in {"GET", "HEAD"}:
        raise HTTPException(403, "组合研究任务只能读取绑定组合")
    if path == "/api/portfolios/session":
        if principal.kind != "user" or scope:
            raise HTTPException(403, "工作台会话仅供本人账号使用")
        # The session bootstrap reads the optional portfolio ACL with this same actor.
        return
    if path == "/api/portfolios/snapshots/daily/recalculations":
        if scope:
            raise HTTPException(403, "绑定的研究或截图任务不能发起估值维护")
        from portfolio_app.api.contracts import DailySnapshotRecalculationRequest
        try:
            payload = DailySnapshotRecalculationRequest.model_validate(await request.json()).model_dump()
        except (ValueError, ValidationError) as error:
            raise HTTPException(422, "重算请求须明确选择组合、标的或全量范围，不能混用") from error
        if not scope and (principal.local_unrestricted or (principal.kind == "service" and "portfolio:maintain" in principal.scopes)):
            return
        ids = payload.get("portfolio_ids") or []
        if payload.get("refresh_all") or payload.get("instrument_ids") or not ids:
            raise HTTPException(403, "全量重算仅供估值维护服务使用；请选择具体组合")
        for selected in ids:
            await run_in_threadpool(require_access, str(selected), "editor", principal)
        return
    if path in {"/api/portfolios", "/api/portfolios/reorder"}:
        if scope and scope.get("kind") != "portfolio":
            raise HTTPException(403, "当前任务不能枚举组合")
        if method == "POST" and path == "/api/portfolios":
            if scope or principal.kind != "user" or (principal.team_role == "reader" and not principal.local_unrestricted):
                raise HTTPException(403, "当前账号不能创建组合")
        return
    if path.endswith("/recover-access") or path == "/api/portfolios/access-recovery":
        if not principal.is_team_owner or principal.kind != "user" or scope:
            raise HTTPException(403, "仅团队拥有者可以显式恢复管理权限")
        return
    if path.endswith("/agent-context") and scope.get("kind") != "capture":
        raise HTTPException(403, "截图上下文仅供绑定的模型任务读取")
    if portfolio_id:
        # Personal presentation settings and read-only computations remain writable by viewers.
        read_operation = method in {"GET", "HEAD"} or "/table-views/" in path or path.endswith(("/preload", "/transaction-imports/preview")) or path == "/api/instrument-risk/review/runs"
        manager_operation = "/members" in path or path.endswith("/member-candidates") or path.endswith("/access-audit") or path.endswith("/copy") or (method == "DELETE" and path == f"/api/portfolios/{portfolio_id}")
        required = "manager" if manager_operation else "viewer" if read_operation else "editor"
        await run_in_threadpool(require_access, str(portfolio_id), required, principal)
        await run_in_threadpool(_capture_scope, request, principal, str(portfolio_id))
        return
    if path.startswith("/api/workspace"):
        raise HTTPException(422, "请选择一个有权访问的组合")
    if path.startswith("/api/instrument-risk"):
        # Watchlist is the authority for team research; preserve the authenticated actor.
        if scope:
            raise HTTPException(403, "组合任务不能修改或读取团队范围资料")
        if method not in {"GET", "HEAD"} and not principal.local_unrestricted and (principal.kind != "user" or principal.team_role == "reader"):
            raise HTTPException(403, "当前账号没有团队研究维护权限")
        return
    raise HTTPException(403, "当前接口未开放给此身份")


from fastapi import Depends

async def portfolio_request_context(request: Request, principal=Depends(authenticated_principal)):
    with principal_context(principal):
        with operation("authorization"):
            await authorize(request, principal)
        if getattr(request.state, "check_financial_generation", False):
            from portfolio_app.services.daily_snapshots import portfolio_financial_read_generation
            portfolio_id = request.path_params.get("portfolio_id") or request.query_params.get("portfolio_id")
            request.state.financial_read_generation = await run_in_threadpool(portfolio_financial_read_generation, str(portfolio_id))
        yield principal
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            portfolio_id = request.path_params.get("portfolio_id")
            if portfolio_id:
                from portfolio_app.db.session import get_session_factory
                from portfolio_app.services.portfolio_access import record_access_event
                def record_operation():
                    with get_session_factory()() as session:
                        record_access_event(session, str(portfolio_id), "api_operation", {"method": request.method, "path": request.url.path}, principal)
                        session.commit()
                await run_in_threadpool(record_operation)
