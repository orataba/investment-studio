"""Human-editable section instructions, separate from analyst findings."""
from pydantic import BaseModel, ConfigDict, Field

SECTION_SKILLS = {
    "overview": "先给增持、减持、维持或无法判断，再给关键依据、机会、重要风险和改变判断的条件。按本轮实际问题决定直接研究或委派独立专员，并行处理可分开的工作；综合实际取得的证据与反证，解释分歧，不强制每轮启动所有角色。每日复核不要求每日改变观点。不得把研究建议写成交易指令。",
    "recent_updates": "标的动态仅筛选影响本标的投资判断的外部新闻与公告、重要市场观点、财报与业绩会、会议纪要。自行计算收益、复核旧持仓、未找到公告、预期报告窗口及研究结论修订不构成新的外部动态，分别保留于量化、主题或覆盖说明。区分事实、观点和传闻；注明真实发生与原始披露日期、来源、重要性和利多利空机制。重要旧事实可按原日期补录，不把复核日当成发生日。连续事项保留具体下一观察条件，一次性事项无需持续跟踪；无实质变化不重复建条目。",
    "themes": "聚焦可研究、可跟踪、影响重要且预计六个月内影响价格的具体催化剂；写清预期差、验证条件、可能影响和失效条件。跟进活跃主题，只记录新的事实和分析。可由事件升级或人工提出；不得自动改变人工暂停、关闭或固定的主题。",
    "quantitative": "比较价格和收益、基本面、分析师同口径预期、估值与量价指标的变化及历史位置。按实际数据选择指标，核对字段单位、真实样本起止和共同样本；百分数不得再按小数收益乘100，区间指标不得改称成立以来。声称修正原数据前先读原字段和值，不把派生解释当原件。ETF、指数及股票公募还要核实实际成分、集中度、广度和重点公司；持仓占净资产、股票资产或披露子集的分母未明时，不推断现金仓位或与配置约束比较。只上报对判断有用的重要变化或风险，保留缺口，不把相关性说成因果。",
}


class SectionSkills(BaseModel):
    model_config = ConfigDict(extra="forbid")
    overview: str = Field(default=SECTION_SKILLS["overview"], min_length=1, max_length=20000)
    recent_updates: str = Field(default=SECTION_SKILLS["recent_updates"], min_length=1, max_length=20000)
    themes: str = Field(default=SECTION_SKILLS["themes"], min_length=1, max_length=20000)
    quantitative: str = Field(default=SECTION_SKILLS["quantitative"], min_length=1, max_length=20000)
