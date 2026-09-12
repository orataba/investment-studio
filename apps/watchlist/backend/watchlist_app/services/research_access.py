"""Access follows the stored conversation/portfolio, including files and AI runs."""
import re
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy import select
from studio_identity import current_principal

from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic


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


def topic_portfolio_ids_by_topic(session, topics):
    # Before account isolation, populated conversations could change portfolio.
    # Retained history and attachments keep every original scope. Query scope
    # scalars only, not the private research text, to authorize aggregate reads.
    portfolio_ids = {topic.topic_id: {topic.portfolio_id} if topic.portfolio_id else set() for topic in topics}
    if not portfolio_ids:
        return portfolio_ids
    scopes = session.execute(select(
        ResearchEntry.topic_id,
        ResearchEntry.context_json["portfolio_id"].as_string(),
        ResearchEntry.context_json["risk_scope"]["portfolio_id"].as_string(),
    ).where(ResearchEntry.topic_id.in_(portfolio_ids))).all()
    for topic_id, portfolio_id, risk_portfolio_id in scopes:
        portfolio_ids[topic_id].update(value for value in (portfolio_id, risk_portfolio_id) if value)
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
        require_entry_access(session, session.get(ResearchEntry, match[1]), tool_write=request.method not in {"GET", "HEAD"})
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
