"""Human-editable section instructions, separate from analyst findings."""
from pydantic import BaseModel, ConfigDict, Field

SECTION_SKILLS = {
    "overview": "先给增持、减持、维持或无法判断，再给关键依据、机会、重要风险和改变判断的条件。综合三个专员的证据与反证，解释分歧；每日复核不要求每日改变观点。不得把研究建议写成交易指令。",
    "recent_updates": "标的动态仅筛选影响本标的投资判断的外部新闻与公告、重要市场观点、财报与业绩会、会议纪要；不收入研究结论修订、主题编辑和运行日志。区分事实、观点和传闻；注明原始日期、来源、重要性和利多利空机制。连续事项保留具体下一观察条件，一次性事项无需持续跟踪；无实质变化不重复建条目。",
    "themes": "聚焦可研究、可跟踪、影响重要且预计六个月内影响价格的具体催化剂；写清预期差、验证条件、可能影响和失效条件。跟进活跃主题，只记录新的事实和分析。可由事件升级或人工提出；不得自动改变人工暂停、关闭或固定的主题。",
    "quantitative": "比较价格和收益、基本面、分析师同口径预期、估值与量价指标的变化及历史位置。按实际数据选择指标，保留单位、期间、共同样本和缺口；ETF、指数及股票公募还要核实实际成分、集中度、广度和重点公司。只上报对判断有用的重要变化或风险，不把相关性说成因果。",
}


class SectionSkills(BaseModel):
    model_config = ConfigDict(extra="forbid")
    overview: str = Field(default=SECTION_SKILLS["overview"], min_length=1, max_length=20000)
    recent_updates: str = Field(default=SECTION_SKILLS["recent_updates"], min_length=1, max_length=20000)
    themes: str = Field(default=SECTION_SKILLS["themes"], min_length=1, max_length=20000)
    quantitative: str = Field(default=SECTION_SKILLS["quantitative"], min_length=1, max_length=20000)
