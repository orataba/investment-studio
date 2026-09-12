from contextlib import asynccontextmanager
from typing import Literal

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from studio_identity import IdentityError, current_principal, principal_context, resolve_request, issue_delegation, cookie_name, local_mode
from briefing_app.access import can_generate, is_published, require_report_access
import re
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from briefing_app.contracts import GenerateRequest, ReportDraft
from briefing_app.db import Report, get_session
from briefing_app.evidence import current_source_index, read_bound_source
from briefing_app.reports import begin_report, report_detail, report_summary, validate_draft
from briefing_app.runner import harness_available, interrupt_incomplete_runs, run_report
from briefing_app.settings import get_settings


@asynccontextmanager
async def lifespan(_app):
    interrupt_incomplete_runs()
    yield


def create_app(settings=None):
    settings = settings or get_settings()
    app = FastAPI(title="Investment Studio Briefing", lifespan=lifespan)
    @app.exception_handler(IdentityError)
    async def identity_error(_request, error):
        return JSONResponse({"detail": error.detail}, status_code=error.status_code, headers={"Cache-Control": "no-store"})

    @app.middleware("http")
    async def identity(request: Request, call_next):
        public = request.method in {"GET", "HEAD"} and (
            request.url.path in {"/health", "/api/briefing/status", "/api/briefing/reports"}
            or re.fullmatch(r"/api/briefing/reports/[^/]+(?:/status)?", request.url.path)
        )
        if request.method == "OPTIONS" or request.url.path == "/health":
            return await call_next(request)
        has_credential = bool(request.headers.get("authorization") or request.cookies.get(cookie_name()))
        request.state.principal = None
        try:
            if public and not has_credential and not local_mode():
                return await call_next(request)
            principal = await run_in_threadpool(resolve_request, request, "briefing", allowed_origins=settings.cors_origins)
            request.state.principal = principal
            with principal_context(principal):
                response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            return response
        except IdentityError as error:
            return await identity_error(request, error)

    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                       allow_credentials=True, allow_methods=["GET", "POST"], allow_headers=["Content-Type", "Authorization"])

    def get_report(report_id: str, session: Session, writing=False, public=False):
        statement = select(Report).where(Report.report_id == report_id)
        report = session.scalar(statement.with_for_update() if writing else statement)
        if report is None:
            raise HTTPException(404, "报告不存在")
        if public:
            if not is_published(report):
                raise HTTPException(404, "报告不存在")
        else:
            require_report_access(report, write=writing)
        return report

    @app.get("/health")
    def health(session: Session = Depends(get_session)):
        session.execute(text("SELECT 1"))
        return {"status": "ok", "app": "briefing"}

    @app.get("/api/briefing/status")
    def status(request: Request):
        allowed = can_generate(request.state.principal, settings.edition_role)
        return {"harness_available": harness_available() if allowed else False, "timezone": settings.timezone,
                "edition_role": settings.edition_role, "can_generate": allowed,
                "can_read_sources": request.state.principal is not None}

    @app.get("/api/briefing/reports")
    def reports(request: Request, report_type: Literal["daily", "weekly"] | None = None, offset: int = Query(0, ge=0),
                limit: int = Query(30, ge=1, le=100), session: Session = Depends(get_session)):
        filters = [Report.report_type == report_type] if report_type else []
        principal = request.state.principal
        if principal is None:
            filters += [Report.status == "completed", Report.input_json["edition_role"].as_string() == "publisher"]
        else:
            filters.append(Report.team_id == principal.team_id)
            if principal.resource_scope:
                if principal.resource_scope.get("kind") != "report":
                    raise HTTPException(403, "任务凭证不能浏览报告列表")
                filters.append(Report.report_id == principal.resource_scope["id"])
            if principal.kind == "service" and not set(principal.scopes) & {"briefing:read", "briefing:publish"}:
                raise HTTPException(403, "此服务身份没有报告权限")
        rows = session.scalars(select(Report).where(*filters).order_by(Report.report_date.desc(), Report.created_at.desc()).offset(offset).limit(limit))
        return {"rows": [report_summary(row) for row in rows], "total": session.scalar(select(func.count()).select_from(Report).where(*filters)),
                "limit": limit, "offset": offset}

    @app.post("/api/briefing/reports", status_code=202)
    def generate(request: GenerateRequest, background: BackgroundTasks, session: Session = Depends(get_session)):
        principal = current_principal()
        if not can_generate(principal, settings.edition_role):
            raise HTTPException(403, "没有生成或发布报告的权限")
        if not harness_available():
            raise HTTPException(503, "尚未配置 DeepSeek 报告运行环境")
        try:
            report, created = begin_report(session, request.report_type, request.cutoff, settings.timezone, edition_role=settings.edition_role, principal=principal)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if created:
            try:
                token = issue_delegation(principal, "briefing", {"kind": "report", "id": report.report_id})
            except IdentityError:
                report.status, report.error = "failed", "未能建立报告任务身份，本轮未开始。"
                session.commit()
                raise
            background.add_task(run_report, report.report_id, token, principal)
        return report_summary(report)

    @app.get("/api/briefing/reports/{report_id}")
    def detail(report_id: str, request: Request, session: Session = Depends(get_session)):
        public = request.state.principal is None
        value = report_detail(get_report(report_id, session, public=public))
        if public:
            # Only sources cited in the finished edition belong to its public projection.
            cited = set()
            def collect(node):
                if isinstance(node, dict):
                    cited.update(node.get("source_ids", []))
                    for child in node.values():
                        collect(child)
                elif isinstance(node, list):
                    for child in node:
                        collect(child)
            collect(value["report"])
            value["sources"] = [source for source in value["sources"] if source["source_id"] in cited]
            value["coverage"] = {"text": {}, "numeric": [], "documents": {}}
        return value

    @app.get("/api/briefing/reports/{report_id}/status")
    def report_status(report_id: str, request: Request, session: Session = Depends(get_session)):
        return report_summary(get_report(report_id, session, public=request.state.principal is None))

    @app.get("/api/briefing/reports/{report_id}/sources/{source_id:path}")
    def source(report_id: str, source_id: str, session: Session = Depends(get_session)):
        report = get_report(report_id, session)
        try:
            return read_bound_source(report.input_json, source_id, settings)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/briefing/reports/{report_id}/context")
    def context(report_id: str, session: Session = Depends(get_session)):
        report = get_report(report_id, session)
        snapshot = report.input_json
        return {**{key: value for key, value in snapshot.items() if key != "sources"},
                "report_id": report_id, "version": report.version,
                "draft_json": report.draft_json["report"] if report.draft_json else None,
                "current_source_index": current_source_index(snapshot),
                "source_total": len(snapshot.get("sources", [])),
                "schema": ReportDraft.model_json_schema()}

    @app.get("/api/briefing/reports/{report_id}/source-index")
    def source_index(report_id: str, offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100),
                     scope: Literal["current", "late_received", "all"] = "current", query: str = "",
                     session: Session = Depends(get_session)):
        sources = get_report(report_id, session).input_json.get("sources", [])
        sources = [source for source in sources if (scope == "all" or source.get("window_scope", "current") == scope)
                   and (not query or query.casefold() in " ".join(str(source.get(key) or "") for key in ("title", "source_name", "entities", "symbol")).casefold())]
        rows = [{key: source.get(key) for key in ("source_id", "source_type", "title", "source_name", "published_at",
                                                "occurred_at", "observed_at", "information_type", "content_completeness", "window_reasons", "symbol", "date") if source.get(key) is not None}
                for source in sources[offset:offset + limit]]
        return {"rows": rows, "total": len(sources), "offset": offset, "limit": limit, "scope": scope, "query": query}

    @app.post("/api/briefing/reports/{report_id}/draft")
    def submit(report_id: str, draft: ReportDraft, mode: Literal["write", "review"] = Query(...),
               session: Session = Depends(get_session)):
        report = get_report(report_id, session, writing=True)
        if report.status != "running":
            raise HTTPException(409, "本轮报告已结束或尚未开始")
        try:
            validated = validate_draft(draft, report.input_json)
        except (ValueError, LookupError) as exc:
            raise HTTPException(422, str(exc)) from exc
        report.draft_json = {"mode": mode, "report": validated}
        session.commit()
        return {"status": "accepted", "mode": mode,
                "message": "校稿已保存；正常结束后由应用发布。" if mode == "review" else "首稿已保存；应用将启动独立校稿。"}

    return app


app = create_app()
