"""Access follows the stored conversation/portfolio, including files and AI runs."""
import re
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy import select
from studio_identity import current_principal

from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.research_scope import JSON_NUL_ESCAPE, research_scope_expression


def require_team_write():
    principal = current_principal()
    if principal.local_unrestricted:
        return principal
    if principal.kind == "service":
        if "watchlist:research" not in principal.scopes:
            raise HTTPException(403, "此服务没有维护团队研究的权限")
    elif principal.team_role not in {"admin", "member"}:
        raise HTTPException(403, "当前账号只能阅读团队研究；可以继续使用个人研究助手")
    return principal


def require_portfolio(portfolio_id):
    from watchlist_app.services.research_workbench import external_json
    try:
        return external_json("portfolio", f"/portfolios/{quote(portfolio_id, safe='')}/access")
    except HTTPException:
        raise
    except OSError as error:
        raise HTTPException(503, "暂时无法确认组合访问权限") from error


def topic_portfolio_ids(session, topic):
    return topic_portfolio_ids_by_topic(session, [topic])[topic.topic_id]


_JSON_NUL_ESCAPE = JSON_NUL_ESCAPE


def _postgres_projection_context():
    from sqlalchemy import JSON, Text, case, cast, func
    text_value = cast(ResearchEntry.context_json, Text)
    affected = case((func.strpos(text_value, r"\u0000") > 0, text_value.op("~")(_JSON_NUL_ESCAPE)), else_=False)
    # PostgreSQL json accepts a retained NUL escape but its extraction functions
    # reject it, even in an unrelated field. Normalize only the SQL working copy;
    # research_projection_rows restores selected values from the untouched JSON.
    safe = case((affected, cast(func.regexp_replace(text_value, _JSON_NUL_ESCAPE, "\\1\ufffd", "g"), JSON)),
                else_=ResearchEntry.context_json)
    return affected, safe


def research_context_projection(session, fields):
    """Parse large PostgreSQL JSON once per row, returning only requested fields."""
    from sqlalchemy import Boolean, JSON, column, func
    if session.get_bind().dialect.name == "postgresql":
        _, safe = _postgres_projection_context()
        relation = func.json_to_record(safe).table_valued(
            *(column(name, kind) for name, kind in fields.items())
        ).render_derived(with_types=True).lateral("run_context")
        return relation, {name: relation.c[name] for name in fields}
    values = {}
    for name, kind in fields.items():
        value = ResearchEntry.context_json[name]
        values[name] = value if kind is JSON else value.as_boolean() if kind is Boolean else value.as_string()
    return None, values


def research_projection_rows(session, query, fields):
    return list(iter_research_projection_rows(session, query, fields))


def iter_research_projection_rows(session, query, fields):
    """Stream one retained context at a time, including PostgreSQL's wire buffer.

    fields maps selected labels to their paths in that original JSON. This keeps
    raw sources, literal backslash-u text and permission fields exact without
    hydrating healthy contexts or changing the ORM identity map/storage.
    Callers that stop early must close this iterator to release its server cursor.
    """
    from types import SimpleNamespace
    from sqlalchemy import Text, and_, case, cast, false, func, or_
    postgres = session.get_bind().dialect.name == "postgresql"
    if postgres:
        affected, _ = _postgres_projection_context()
        # The SQL working copy replaces each actual NUL with a literal U+FFFD.
        # An unaffected selected field is already exact even when an unrelated
        # retained source contains NUL. Restore the original only if a selected
        # value could contain a replacement. A pre-existing U+FFFD merely causes
        # a conservative extra read; it never changes the returned value.
        selected = query.selected_columns
        needs_original = or_(*(func.strpos(cast(selected[name], Text), "\ufffd") > 0
                               for name in fields)) if fields else false()
        query = query.add_columns(case((and_(affected, needs_original), ResearchEntry.context_json))
                                  .label("_original_context"))
    result = session.execute(query.execution_options(yield_per=1))
    try:
        for row in result.mappings():
            data = dict(row)
            original = data.pop("_original_context", None)
            if original is not None:
                for name, path in fields.items():
                    value = original
                    for key in path:
                        value = value.get(key) if isinstance(value, dict) else None
                    data[name] = value
            yield SimpleNamespace(**data, _mapping=data)
    finally:
        result.close()


def instrument_run_scope(session, instrument_id):
    """Exact overlap with requested IDs, independent of mutable topic scope."""
    from sqlalchemy import Text, case, func, literal
    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import ARRAY
        ids = [instrument_id] if isinstance(instrument_id, str) else list(instrument_id)
        return research_scope_expression(ResearchEntry.context_json).overlap(literal(ids, type_=ARRAY(Text())))
    scope = ResearchEntry.context_json["instrument_ids"]
    array = case((func.json_type(scope) == "array", scope), else_="[]")
    elements = func.json_each(array).table_valued("value")
    predicate = (elements.c.value == instrument_id if isinstance(instrument_id, str)
                 else elements.c.value.in_(instrument_id))
    return select(1).select_from(elements).where(predicate).correlate_except(elements).exists()


def topic_portfolio_ids_by_topic(session, topics):
    # Before account isolation, populated conversations could change portfolio.
    # Retained history and attachments keep every original scope. Query scope
    # scalars only, not the private research text, to authorize aggregate reads.
    portfolio_ids = {topic.topic_id: {topic.portfolio_id} if topic.portfolio_id else set() for topic in topics}
    if not portfolio_ids:
        return portfolio_ids
    from sqlalchemy import JSON, String, true
    relation, values = research_context_projection(session, {"portfolio_id": String, "risk_scope": JSON})
    query = select(ResearchEntry.topic_id, values["portfolio_id"].label("portfolio_id"),
                   values["risk_scope"]["portfolio_id"].as_string().label("risk_portfolio_id")).select_from(ResearchEntry)
    if relation is not None:
        query = query.join(relation, true())
    query = query.where(ResearchEntry.topic_id.in_(portfolio_ids))
    if session.get_bind().dialect.name == "postgresql":
        from watchlist_app.db.research_scope import research_portfolio_scope_expression
        # All historical kinds remain eligible. The partial index skips only
        # contexts with no possible portfolio scope; the original projection
        # still supplies exact IDs, including retained NUL/literal escape values.
        query = query.where(research_portfolio_scope_expression(ResearchEntry.context_json))
    scopes = iter_research_projection_rows(session, query,
        {"portfolio_id": ("portfolio_id",), "risk_portfolio_id": ("risk_scope", "portfolio_id")})
    for row in scopes:
        portfolio_ids[row.topic_id].update(value for value in (row.portfolio_id, row.risk_portfolio_id) if value)
    return portfolio_ids


def require_team_publication_scope(session, run):
    topic = session.get(ResearchTopic, run.topic_id)
    context = run.context_json or {}
    if (context.get("portfolio_id") or (context.get("risk_scope") or {}).get("portfolio_id")
            or (topic and topic_portfolio_ids(session, topic))):
        raise ValueError("组合对话及其历史不能直接发布到团队研究，请另行撰写要分享的通用判断")


def require_topic_access(session, topic, *, principal=None):
    principal = principal or current_principal()
    if topic is None:
        raise HTTPException(404, "对话或研究记录不存在")
    if principal.local_unrestricted:
        return topic
    if topic.team_id != principal.team_id:
        raise HTTPException(404, "对话或研究记录不存在")
    if topic.visibility == "private" and topic.created_by_user_id != principal.user_id:
        raise HTTPException(404, "对话或研究记录不存在")
    if topic.visibility == "private" and principal.user_id is None:
        raise HTTPException(404, "对话或研究记录不存在")
    for portfolio_id in sorted(topic_portfolio_ids(session, topic)):
        require_portfolio(portfolio_id)
    return topic


def require_entry_access(session, entry, *, tool_write=False):
    principal = current_principal()
    if entry is None:
        raise HTTPException(404, "研究记录不存在")
    scope = principal.resource_scope
    if scope and (scope.get("kind") != "run" or scope.get("id") != entry.entry_id):
        raise HTTPException(403, "运行凭证不能访问其他研究记录")
    if scope:
        actor = (entry.context_json or {}).get("research_actor") or {}
        subject_matches = (actor.get("user_id") == principal.user_id and principal.kind == "user" and principal.user_id is not None)
        if principal.kind == "service":
            subject_matches = actor.get("kind") == "service" and actor.get("service_id") == principal.service_id
        if not subject_matches:
            raise HTTPException(403, "运行凭证与任务发起人不一致")
    if tool_write and (not scope or scope.get("kind") != "run"):
        raise HTTPException(403, "模型工具仅接受本轮运行凭证")
    if not principal.local_unrestricted and entry.team_id != principal.team_id:
        raise HTTPException(404, "研究记录不存在")
    topic = session.get(ResearchTopic, entry.topic_id)
    require_topic_access(session, topic, principal=principal)
    return entry


def _entry_access_projection(session, entry_id):
    from types import SimpleNamespace
    from sqlalchemy import JSON, true
    relation, fields = research_context_projection(session, {"research_actor": JSON})
    query = select(ResearchEntry.entry_id, ResearchEntry.topic_id, ResearchEntry.team_id,
                   fields["research_actor"].label("research_actor")).select_from(ResearchEntry)
    if relation is not None:
        query = query.join(relation, true())
    rows = research_projection_rows(session, query.where(ResearchEntry.entry_id == entry_id),
                                    {"research_actor": ("research_actor",)})
    row = rows[0] if rows else None
    return (SimpleNamespace(entry_id=row.entry_id, topic_id=row.topic_id, team_id=row.team_id,
            context_json={"research_actor": row.research_actor}) if row else None)


def visible_topics(session):
    principal = current_principal()
    query = select(ResearchTopic).where(ResearchTopic.visibility == "private")
    if not principal.local_unrestricted:
        query = query.where(ResearchTopic.team_id == principal.team_id, ResearchTopic.created_by_user_id == principal.user_id)
    rows = session.scalars(query.order_by(ResearchTopic.updated_at.desc()))
    visible = []
    for topic in rows:
        try:
            require_topic_access(session, topic)
        except HTTPException as error:
            if error.status_code in {403, 404}:
                continue
            raise
        visible.append(topic)
    return visible


def enforce_request(request, session):
    """One boundary for every API; object authorization stays next to stored scope."""
    principal = current_principal()
    path = request.url.path
    match = re.fullmatch(r"/api/research/runs/([^/]+)(?:/.*)?", path)
    if principal.resource_scope:
        if not match or principal.resource_scope != {"kind": "run", "id": match[1]}:
            raise HTTPException(403, "运行凭证仅用于对应研究任务")
    if match:
        source_directory = (request.method == "GET" and path.endswith("/context")
                            and request.query_params.get("section") == "sources")
        entry = (_entry_access_projection(session, match[1]) if source_directory
                 else session.get(ResearchEntry, match[1]))
        require_entry_access(session, entry, tool_write=request.method not in {"GET", "HEAD"})
        return
    if principal.local_unrestricted:
        return
    if principal.kind == "service":
        if "watchlist:maintenance" in principal.scopes and path == "/api/recalc/bulk" and request.method == "POST":
            return
        if "watchlist:research" not in principal.scopes:
            raise HTTPException(403, "此服务没有访问当前研究功能的权限")
        if request.method not in {"GET", "HEAD"} and path not in {"/api/sector-research/runs", "/api/risk/review/runs"}:
            raise HTTPException(403, "研究服务不能代替投资经理维护正式观点或团队资料")
    if path == "/api/risk/review/runs" and request.method == "POST":
        # The risk service distinguishes portfolio-read analysis from shared team writes.
        return
    if request.method in {"GET", "HEAD", "OPTIONS"} or path == "/api/screener/query":
        return
    # Personal discussion and display settings never modify shared research.
    personal = re.fullmatch(r"/api/research/topics(?:/[^/]+(?:/(?:analysis|entries|files))?)?", path)
    personal_entry = re.fullmatch(r"/api/research/entries/[^/]+/completion", path)
    preferences = path == "/api/watchlists/reorder" or re.fullmatch(r"/api/watchlists/[^/]+/views(?:/[^/]+)?", path)
    if personal or personal_entry or preferences:
        if principal.kind != "user":
            raise HTTPException(403, "服务账号不能创建个人对话")
        return
    require_team_write()
