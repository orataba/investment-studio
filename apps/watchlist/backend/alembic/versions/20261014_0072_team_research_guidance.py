"""Team research framework and instrument-specific supplements."""
from copy import deepcopy
from datetime import UTC, datetime
from alembic import op
import sqlalchemy as sa

revision = "20261014_0072"
down_revision = "20261012_0071"
branch_labels = None
depends_on = None

# Frozen former defaults: never import mutable application instructions here.
LEGACY_SECTION_SKILLS = {'overview': '先给增持、减持、维持或无法判断，再给关键依据、机会、重要风险和改变判断的条件。按本轮实际问题决定直接研究或委派独立专员，并行处理可分开的工作；综合实际取得的证据与反证，解释分歧，不强制每轮启动所有角色。每日复核不要求每日改变观点。不得把研究建议写成交易指令。',
 'recent_updates': '标的动态仅筛选影响本标的投资判断的外部新闻与公告、重要市场观点、财报与业绩会、会议纪要。自行计算收益、复核旧持仓、未找到公告、预期报告窗口及研究结论修订不构成新的外部动态，分别保留于量化、主题或覆盖说明。区分事实、观点和传闻；注明真实发生与原始披露日期、来源、重要性和利多利空机制。重要旧事实可按原日期补录，不把复核日当成发生日。连续事项保留具体下一观察条件，一次性事项无需持续跟踪；无实质变化不重复建条目。',
 'themes': '聚焦可研究、可跟踪、影响重要且预计六个月内影响价格的具体催化剂；写清预期差、验证条件、可能影响和失效条件。跟进活跃主题，只记录新的事实和分析。可由事件升级或人工提出；不得自动改变人工暂停、关闭或固定的主题。',
 'quantitative': '比较价格和收益、基本面、分析师同口径预期、估值与量价指标的变化及历史位置。按实际数据选择指标，核对字段单位、真实样本起止和共同样本；百分数不得再按小数收益乘100，区间指标不得改称成立以来。声称修正原数据前先读原字段和值，不把派生解释当原件。ETF、指数及股票公募还要核实实际成分、集中度、广度和重点公司；持仓占净资产、股票资产或披露子集的分母未明时，不推断现金仓位或与配置约束比较。只上报对判断有用的重要变化或风险，保留缺口，不把相关性说成因果。'}


def promote_default_skills(context, entry_id, created_at, updated_at, now):
    original = context.get("mandate") or {}
    skills = original.get("section_skills") or {}
    changed = [key for key, text in LEGACY_SECTION_SKILLS.items() if skills.get(key) == text]
    if not changed:
        return None
    value = deepcopy(context)
    revision = value.get("revision", 1)
    prior = {**deepcopy(original), "version_id": f"{entry_id}:v{revision}",
        "author": deepcopy(value.get("author")), "guidance_author": deepcopy(value.get("guidance_author")),
        "guidance_updated_at": value.get("guidance_updated_at"), "user_focus": deepcopy(value.get("user_focus", [])),
        "created_at": created_at.isoformat() if created_at else None,
        "updated_at": updated_at.isoformat() if updated_at else None}
    value["versions"] = [*value.get("versions", []), prior]
    value["revision"] = revision + 1
    value["mandate"]["section_skills"] = {**skills, **{key: "" for key in changed}}
    value["author"] = {"origin": "migration", "display_name": "团队研究框架迁移", "migration": revision_id}
    value["migration"] = {"revision": revision_id, "at": now.isoformat(), "inherited_sections": changed,
        "reason": "原系统默认提升为团队通用框架；非默认人工内容保留为标的补充。"}
    return value


revision_id = revision


def upgrade():
    op.create_table("research_guidance",
        sa.Column("team_id", sa.Text(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("guidance", sa.Text(), nullable=False),
        sa.Column("section_skills", sa.JSON(), nullable=False),
        sa.Column("author", sa.JSON(), nullable=False),
        sa.Column("versions", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    entries = sa.table("research_entry", sa.column("entry_id", sa.String()), sa.column("kind", sa.String()),
        sa.column("context_json", sa.JSON()), sa.column("read_context_json", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)), sa.column("updated_at", sa.DateTime(timezone=True)))
    connection, now = op.get_bind(), datetime.now(UTC)
    rows = connection.execute(sa.select(entries).where(entries.c.kind == "note",
        entries.c.entry_id.like("dossier-mandate:%"))).mappings().all()
    for row in rows:
        context = row["context_json"] or {}
        if context.get("role") != "research_mandate":
            continue
        value = promote_default_skills(context, row["entry_id"], row["created_at"], row["updated_at"], now)
        if value is not None:
            connection.execute(entries.update().where(entries.c.entry_id == row["entry_id"]).values(
                context_json=value, read_context_json={**(row["read_context_json"] or {}),
                    "author": value["author"]}, updated_at=now))


def downgrade():
    raise RuntimeError("团队研究指导及人工版本历史不可自动降级；请保留数据并使用经过审查的前向迁移。")
