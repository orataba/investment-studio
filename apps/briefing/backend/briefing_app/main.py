from contextlib import asynccontextmanager
from typing import Literal

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from studio_identity import IdentityError, current_principal, principal_context, resolve_request, issue_delegation, cookie_name, local_mode
from studio_runtime import operation
from briefing_app.access import can_generate, is_published, require_report_access
import re
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import case, func, select, text
from sqlalchemy.orm import Session, load_only

from briefing_app.contracts import GenerateRequest, ReportDraft
from briefing_app.db import Report, ReportSource, get_session
from briefing_app.evidence import current_source_index, read_bound_source
from briefing_app.reports import begin_report, report_detail, report_summary, validate_draft, summary_statement, projected_report_summary
from studio_runtime.model_work import model_work_status, require_model_work
from briefing_app.runner import harness_available, interrupt_incomplete_runs, run_report
from briefing_app.settings import get_settings
from briefing_app.scheduling import read_schedules


@asynccontextmanager
async def lifespan(_app):
    interrupt_incomplete_runs()
    yield


def create_app(settings=None):
    settings = settings or get_settings()
    app = FastAPI(title="Investment Studio Briefing", version="1.2.0", lifespan=lifespan)
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
            with operation("identity"):
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

    def get_report(report_id: str, session: Session, writing=False, public=False, reading=False, metadata_only=False):
        statement = select(Report).where(Report.report_id == report_id)
        if metadata_only:
            statement = statement.options(load_only(Report.report_id, Report.team_id, Report.edition_role, Report.status, raiseload=True))
        elif reading:
            statement = statement.options(load_only(*(getattr(Report, key) for key in ("report_id", "team_id", "report_type", "report_date", "version", "status", "cutoff", "created_at", "completed_at", "error", "source_count", "edition_role", "reading_json", "result_json")), raiseload=True))
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
    def status(request: Request, session: Session = Depends(get_session)):
        allowed = can_generate(request.state.principal, settings.edition_role)
        filters = report_access_filters(request.state.principal)
        editions = {}
        for kind in ("daily", "weekly"):
            statement = summary_statement().where(*filters, Report.report_type == kind).order_by(Report.report_date.desc(), Report.created_at.desc()).limit(1)
            latest = session.execute(statement).first()
            completed = session.execute(statement.where(Report.status == "completed")).first()
            editions[kind] = {"latest": projected_report_summary(latest) if latest else None,
                              "latest_completed": projected_report_summary(completed) if completed else None}
        return {"harness_available": harness_available() if allowed else False, "timezone": settings.timezone,
                "edition_role": settings.edition_role, "can_generate": allowed,
                "can_read_sources": request.state.principal is not None, "model_work": model_work_status(),
                "schedules": read_schedules(settings.edition_role), "editions": editions}

    def report_access_filters(principal):
        if principal is None:
            return [Report.status == "completed", Report.edition_role == "publisher"]
        filters = [Report.team_id == principal.team_id]
        if principal.resource_scope:
            if principal.resource_scope.get("kind") != "report":
                raise HTTPException(403, "任务凭证不能浏览报告列表")
            filters.append(Report.report_id == principal.resource_scope["id"])
        if principal.kind == "service" and not set(principal.scopes) & {"briefing:read", "briefing:publish"}:
            raise HTTPException(403, "此服务身份没有报告权限")
        return filters

    @app.get("/api/briefing/reports")
    def reports(request: Request, report_type: Literal["daily", "weekly"] | None = None, offset: int = Query(0, ge=0),
                limit: int = Query(30, ge=1, le=100),
                month: str | None = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
                query: str | None = Query(None, max_length=120), session: Session = Depends(get_session)):
        filters = [Report.report_type == report_type] if report_type else []
        if month:
            filters.append(Report.report_date.startswith(f"{month}-", autoescape=True))
        if query and query.strip():
            term = query.strip()
            # Titles are fixed UI labels, not retained report-body text. Match
            # both interface languages without hydrating the report JSON.
            zh_title = case((Report.report_type == "daily", "投研日报"), else_="投研周报") + " | " + Report.report_date
            en_title = case((Report.report_type == "daily", "Daily research briefing"), else_="Weekly research briefing") + " | " + Report.report_date
            filters.append(zh_title.icontains(term, autoescape=True) | en_title.icontains(term, autoescape=True))
        filters += report_access_filters(request.state.principal)
        rows = session.execute(summary_statement().where(*filters).order_by(Report.report_date.desc(), Report.created_at.desc()).offset(offset).limit(limit))
        return {"rows": [projected_report_summary(row) for row in rows], "total": session.scalar(select(func.count()).select_from(Report).where(*filters)),
                "limit": limit, "offset": offset}

    @app.post("/api/briefing/reports", status_code=202)
    def generate(request: GenerateRequest, background: BackgroundTasks, session: Session = Depends(get_session)):
        principal = current_principal()
        if not can_generate(principal, settings.edition_role):
            raise HTTPException(403, "没有生成或发布报告的权限")
        require_model_work()
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
        value = report_detail(get_report(report_id, session, public=public, reading=True))
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
        row = session.execute(summary_statement().where(Report.report_id == report_id)).first()
        if row is None or (request.state.principal is None and (row.status != "completed" or row.edition_role != "publisher")):
            raise HTTPException(404, "报告不存在")
        if request.state.principal is not None:
            require_report_access(row)
        return projected_report_summary(row)

    @app.get("/api/briefing/reports/{report_id}/sources/{source_id:path}")
    def source(report_id: str, source_id: str, session: Session = Depends(get_session)):
        report = get_report(report_id, session, reading=True)
        payload = session.scalar(select(ReportSource.payload_json).where(ReportSource.report_id == report_id, ReportSource.source_id == source_id).order_by(ReportSource.ordinal).limit(1))
        try:
            return read_bound_source({"cutoff": report.reading_json.get("cutoff") or report.cutoff.isoformat(), "sources": [payload] if payload else []}, source_id, settings)
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
                     source_type: str | None = None, completeness: str = "all",
                     session: Session = Depends(get_session)):
        get_report(report_id, session, metadata_only=True)
        filters = [ReportSource.report_id == report_id]
        if scope != "all":
            filters.append(ReportSource.window_scope == scope)
        if source_type:
            filters.append(ReportSource.source_type == source_type)
        unfiltered_total = session.scalar(select(func.count()).select_from(ReportSource).where(*filters))
        if query.strip():
            filters.append(ReportSource.search_text.contains(query.strip().casefold(), autoescape=True))
        if completeness != "all":
            filters.append(ReportSource.completeness == completeness)
        rows = session.scalars(select(ReportSource.metadata_json).where(*filters).order_by(ReportSource.ordinal).offset(offset).limit(limit)).all()
        return {"rows": rows, "total": session.scalar(select(func.count()).select_from(ReportSource).where(*filters)),
                "unfiltered_total": unfiltered_total, "offset": offset, "limit": limit, "scope": scope, "query": query}

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

    from studio_runtime import install_diagnostics
    install_diagnostics(app, "briefing")
    return app


app = create_app()
