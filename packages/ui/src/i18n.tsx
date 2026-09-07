import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { systemMessages, systemSourceAliases } from './systemMessages'
import { withLanguage } from './navigation'

export const LANGUAGE_STORAGE_KEY = 'investment_studio.language'
export const LANGUAGE_COOKIE_NAME = 'investment_studio_language'

export const supportedLanguages = [
  { value: 'en', label: 'English', shortLabel: 'EN', htmlLang: 'en' },
  { value: 'zh-Hans', label: '简体中文', shortLabel: '简中', htmlLang: 'zh-Hans' },
] as const

export type SupportedLanguage = (typeof supportedLanguages)[number]['value']

export type LanguageMessages = Partial<Record<SupportedLanguage, Record<string, string>>>

export type LanguagePattern = {
  match: RegExp
  replace: string | ((...captures: string[]) => string)
}

export type LanguagePatternMessages = Partial<Record<SupportedLanguage, LanguagePattern[]>>

type InterpolationValues = Record<string, string | number | null | undefined>

type LanguageContextValue = {
  language: SupportedLanguage
  setLanguage: (language: SupportedLanguage) => void
  t: (message: string, values?: InterpolationValues) => string
}

type LanguageProviderProps = {
  children: ReactNode
  messages?: LanguageMessages
  patterns?: LanguagePatternMessages
  enableDomTranslation?: boolean
}

const baseMessages: LanguageMessages = {
  'zh-Hans': {
    Language: '语言',
    English: 'English',
    'Simplified Chinese': '简体中文',
    Home: '首页',
    Watchlist: '关注列表',
    Portfolio: '组合',
    'Investment Studio': 'Investment Studio',
    Regime: '市场状态',
    'Sign out': '退出',
    Workspace: '工作区',
    'Choose where to work.': '选择工作区。',
    'Research markets, monitor assets, and manage your portfolios.':
      '研究市场、跟踪资产、管理投资组合。',
    'Research and monitoring': '研究与监控',
    'Review funds, indexes, watchlists, and instrument research.':
      '查看基金、指数、关注列表和标的研究。',
    'Manage holdings, transactions, performance, risk, and research.':
      '管理持仓、交易、绩效、风险和组合研究。',
    'Shared instruments and market data': '共享标的与市场数据',
    'Market regime': '市场状态',
    'Review current market regimes, signals, and release evidence.': '查看当前市场状态、信号与发布依据。',
    'Registered Assets': '已登记资产',
    'Searching registered assets…': '正在搜索已登记资产…',
    'Shared Asset Data': '共享资产数据',
    'Select an existing asset to add to this watchlist. New assets and market-data sources are maintained through the backend CLI. Classification and research remain in Watchlist.':
      '选择已有资产加入关注列表。新增资产及数据来源通过后台命令行维护，分类与研究仍在关注列表中进行。',
    API: 'API',
    Open: '打开',
    Search: '搜索',
    Type: '类型',
    Coverage: '覆盖',
    Source: '来源',
    Sort: '排序',
    Actions: '操作',
    Status: '状态',
    'Instrument Type': '资产类别',
    Taxonomy: '分类体系',
    'Data Freshness Status': '数据新鲜度',
    'Coverage Status': '状态',
    'Research lifecycle status such as watch, proposed, invested, paused, or exited.':
      '投研生命周期状态，例如观察、拟投、在投、暂停或退出。',
    Watch: '观察',
    Proposed: '拟投',
    Invested: '在投',
    Paused: '暂停',
    Exited: '退出',
    Unspecified: '未设置',
    Focus: '重点跟踪',
    Currency: '币种',
    Name: '名称',
    Date: '日期',
    Value: '数值',
    Provider: '提供方',
    Field: '字段',
    Rate: '汇率',
    Pair: '货币对',
    Family: '类别',
    Basis: '口径',
    Role: '角色',
    Identifier: '标识',
    Lifecycle: '生命周期',
    Complete: '完整',
    Partial: '部分',
    Unavailable: '不可用',
    Active: '启用',
    Archived: '已归档',
    Manual: '手动',
    Email: '邮件',
    Direct: '直接',
    Inverse: '反向',
    Cross: '交叉',
    Equity: '股票',
    Fund: '基金',
    Bond: '债券',
    Cash: '现金',
    Other: '其他',
    Trading: '交易',
    Valuation: '估值',
    'Total Return': '总回报',
    Chart: '图表',
    Reference: '参考',
    'Last Trade': '最新成交',
    'Adjusted Close': '复权收盘',
    'Clean Price': '净价',
    'Dirty Price': '全价',
    Par: '面值',
    Spot: '即期',
    'Unit NAV': '单位净值',
    'Cumulative NAV': '复权累计净值',
    'Dividend-Reinvested Total Return NAV': '分红再投资复权累计净值',
    Overview: '总览',
    Holdings: '持仓',
    Performance: '业绩',
    Risk: '风险',
    Price: '价格',
    Fundamentals: '基本面',
    Events: '事件',
    Methodology: '方法论',
    Fees: '费用',
    Management: '管理团队',
    Organization: '机构与团队',
    Terms: '条款',
    Exposure: '持仓',
    People: '团队',
    Strategy: '策略',
    Transactions: '交易',
    Accounts: '账户',
    Review: '复盘',
    Research: '研究',
    Taxonomies: '分类',
    Monitoring: '监控',
    Documents: '文档',
    Instruments: '标的',
    Securities: '证券',
    Watchlists: '关注列表',
    'All Watchlists': '全部关注列表',
    'Sort By: Name': '排序：名称',
    'Watchlist name': '关注列表名称',
    'Copy Watchlist': '复制关注列表',
    'Delete Watchlist': '删除关注列表',
    'Create Watchlist': '创建关注列表',
    'Drag to reorder': '拖动排序',
    'Drag rows to reorder watchlists.': '拖动条目可调整关注列表顺序。',
    'Failed to load watchlists.': '关注列表加载失败。',
    'Failed to reorder watchlists.': '关注列表排序保存失败。',
    'Failed to create watchlist.': '关注列表创建失败。',
    'Failed to copy watchlist.': '关注列表复制失败。',
    'Failed to delete watchlist.': '关注列表删除失败。',
    'All': '全部',
    'Portfolio actions': '组合操作',
    'Copy Portfolio': '复制组合',
    'Delete Portfolio': '删除组合',
    '+ Create Portfolio': '+ 创建组合',
    'View: Portfolio Summary': '视图：组合摘要',
    'Portfolio id is required.': '缺少组合 ID。',
    'Failed to load workspace summary.': '工作区摘要加载失败。',
    'Workspace summary unavailable': '工作区摘要不可用',
    'Failed to copy portfolio.': '组合复制失败。',
    'Failed to delete portfolio.': '组合删除失败。',
    'As of': '截至',
    'Portfolio sections': '组合栏目',
    'Shared database ops': '共享数据库运营',
    'Fund research and monitoring': '基金研究与监控',
    'Portfolio management': '组合管理',
    ready: '可用',
    'frontend env fallback': '前端环境兜底',
    'backend unavailable': '后端不可用',
    'platform backend': '平台后端',
    'Complete Coverage': '完整覆盖',
    'Use Case': '使用场景',
    'Shared instrument / FX / NAV ops': '共享标的 / 汇率 / 净值运营',
    'Shared instruments, FX, NAV imports, email refresh rules, and other shared market data operations.':
      '共享标的、汇率、净值导入、邮件刷新规则以及其他共享市场数据运营。',
    'Fund-only watchlists, fund detail pages, facts ingest, read models, and monitoring workflows.':
      '基金关注列表、基金详情页、事实导入、读模型和监控工作流。',
    'Portfolio, account, transaction, performance, risk, and research workflows built on top of the shared instrument core.':
      '基于共享资产核心构建的组合、账户、交易、绩效、风险和研究工作流。',
    'Shared Data Operations': '共享数据运营',
    Total: '总计',
    Funds: '基金',
    'Funds With NAV': '有净值基金',
    'Funds With Quote': '有报价基金',
    'Find Assets': '查找资产',
    'Code / name / asset id / identifier': '代码 / 名称 / 资产 ID / 标识',
    'All Types': '全部类型',
    'All Sources': '全部来源',
    'All Assets': '全部资产',
    'Has NAV': '已有净值',
    'Missing NAV': '缺少净值',
    'Latest NAV Date': '最新净值日期',
    'Has Quote': '已有报价',
    'Missing Quote': '缺少报价',
    'Latest Quote Date': '最新报价日期',
    'Funds Only': '仅基金',
    'Reset Filters': '重置筛选',
    'No NAV loaded yet': '尚未加载净值',
    'Not NAV-based': '非净值型',
    'No selected quotes': '无选中报价',
    'Add Quote': '添加报价',
    'Import NAV': '导入净值',
    Refresh: '刷新',
    'Close Action': '关闭',
    'Close Detail': '关闭详情',
    Operations: '操作',
    'Add Asset': '添加资产',
    'Source Settings': '来源设置',
    'Update FX': '更新汇率',
    'Refresh Selected': '刷新所选',
    'Hide Archived': '隐藏归档',
    'Show Archived': '显示归档',
    'Close Panel': '关闭面板',
    'Primary Identifier Type': '主标识类型',
    'Primary Identifier': '主标识',
    'Create Asset': '创建资产',
    'Selected Asset': '已选资产',
    'Save Quote': '保存报价',
    'Source Mode': '来源模式',
    'Email Source': '邮件来源',
    'API Profile': 'API 配置',
    'Folder / Rule': '文件夹 / 规则',
    'Save Source Settings': '保存来源设置',
    'Refresh Now': '立即刷新',
    'Upload Excel / CSV': '上传 Excel / CSV',
    Upload: '上传',
    'Upload...': '上传中...',
    'Uploading...': '上传中...',
    Uploading: '上传中',
    'File Name': '文件名',
    Notes: '备注',
    Size: '大小',
    Uploaded: '已上传',
    'Fact Sheet': '要素表',
    'Valuation Statement': '估值表',
    'Due Diligence Report': '尽调报告',
    'Investment Memo': '投资备忘录',
    'Fund Contract': '基金合同',
    Prospectus: '招募说明书',
    'Monthly / Quarterly Report': '月报/季报',
    'Analyst Stance': '投研观点',
    Peer: '同类',
    'Expense Ratios & Fees': '费率与费用',
    'Adjusted Expense Ratio': '调整后费率',
    'Reported Expense Ratio': '披露费率',
    'Management Fee': '管理费',
    'Interest Expense Fees': '利息费用',
    'Redemption Fee': '赎回费',
    'Minimum Initial Investment': '最低起投金额',
    'Distribution Policy': '分配政策',
    'Policy Text': '政策说明',
    'Fee Notes': '费用备注',
    Edit: '编辑',
    'Management Company': '管理人',
    'Sub-Advisor': '子顾问',
    'Management Profile': '管理人信息',
    'Fund Managers / Research Team': '基金经理 / 研究团队',
    'Additional Fields': '其他字段',
    'Investment Scope / Objective': '投资范围 / 目标',
    'Investment Strategy': '投资策略',
    'Investment Process': '投资流程',
    'Risk Controls': '风控措施',
    'Save People': '保存团队',
    'Edit People': '编辑团队',
    'Save Strategy': '保存策略',
    'Edit Strategy': '编辑策略',
    'Save Documents': '保存文档',
    'Edit Documents': '编辑文档',
    'Save Research': '保存研究',
    'Edit Research': '编辑研究',
    'Save Note': '保存备注',
    'Document uploaded.': '文档已上传。',
    'Please choose a file to upload.': '请先选择要上传的文件。',
    'Failed to upload document.': '文档上传失败。',
    'No documents yet.': '暂无文档。',
    Title: '标题',
    'Select type': '选择类型',
    'Optional note': '可选备注',
    Settings: '设置',
    'Download PDF': '下载 PDF',
    'Instrument Detail': '标的详情',
    'Stock Detail': '股票详情',
    'ETF Detail': 'ETF 详情',
    'Index Detail': '指数详情',
    'Company Profile': '公司概况',
    'Fund Profile': '基金概况',
    'Fund Reference Profile': '基金参考资料',
    'Provider facts separated from internal research conclusions': '数据源事实与内部研究结论分开维护',
    'Reference provider': '参考数据源',
    'Fund company': '基金管理人',
    Custodian: '托管人',
    'Custodian Fee': '托管费',
    'Sales Service Fee': '销售服务费',
    'Subscription Fee': '申购费',
    'Asset / fund type': '资产 / 基金类型',
    Benchmark: '基准',
    Inception: '成立日期',
    'Research rating': '研究评级',
    'No manual research rating': '尚未设置人工研究评级',
    Latest: '最新',
    'Current month': '本月',
    'Year to date': '年初至今',
    'From peak': '距离高点',
    'Taxonomy peers': '分类同类',
    'Compare benchmark': '对比基准',
    'Show benchmark choices': '显示基准选项',
    'Cumulative Total Return': '累计总回报',
    Drawdown: '回撤',
    Period: '区间',
    'Provider Fee Facts': '数据源费率信息',
    'Source-reported values kept separate from the internal research record': '来源披露值与内部研究记录分开维护',
    'Operational Data Contract': '运营数据约定',
    'NAV delivery, cadence and source status': '净值交付、频率与来源状态',
    'Organization & Key Persons': '机构与关键人员',
    'Sector Allocation': '行业配置',
    'Country Allocation': '国家与地区配置',
    'Latest Holdings': '最新持仓',
    'Income Statements · FMP': '利润表 · FMP',
    'Key Metrics · FMP': '关键指标 · FMP',
    'Valuation & Financial Ratios · FMP': '估值与财务比率 · FMP',
    Dividends: '分红',
    'Share Splits': '拆股',
    'Index Profile': '指数概况',
    'Index Source Contract': '指数数据源约定',
    'Methodology Coverage': '方法论覆盖',
    'Reference Data Coverage': '参考数据覆盖',
    Stock: '股票',
    Index: '指数',
    Detail: '详情',
    'Instrument Settings': '标的设置',
    'Investment Status': '投资状态',
    'Research Stage': '研究阶段',
    'Risk Attention': '风险关注',
    'No Trigger': '暂无触发',
    'Watching': '观察中',
    'Researching': '研究中',
    'Candidate': '候选',
    'Limited': '监测受限',
    'Set this specific instrument to Watch, Proposed, Invested, Paused, or Exited.':
      '为这个具体标的设置观察、拟投、在投、暂停或退出状态。',
    'Taxonomy Settings': '分类设置',
    'Classification Path': '分类路径',
    'Current Path': '当前路径',
    'Current path': '当前路径',
    Unclassify: '取消分类',
    Unclassified: '未分类',
    'Stop here': '停在此级',
    'Select parent first': '请先选择上一级',
    Save: '保存',
    Saving: '保存中',
    'Saving…': '保存中…',
    'Loading settings…': '正在加载设置…',
    Cancel: '取消',
    'Only two fund NAV series are accepted: Unit NAV and Dividend-Reinvested Total Return NAV.':
      '仅接受两种基金净值口径：单位净值和分红再投资复权累计净值。',
    'Only two fund NAV series are accepted: Unit NAV and Cumulative NAV.':
      '仅接受两种基金净值口径：单位净值和复权累计净值。',
    Cautious: '谨慎',
    'High Conviction': '高置信',
    Positive: '积极',
    Unrated: '未评级',
    'Pasted Rows': '粘贴行',
    'Preview Parsed Rows': '预览解析行',
    'Import NAV Rows': '导入净值行',
    'Refresh From Source': '从来源刷新',
    Code: '代码',
    'Spot Rate': '即期汇率',
    'Save FX Rate': '保存汇率',
    Include: '包含',
    'Include Archived': '包含归档',
    'Show Active Only': '仅显示启用',
    'Latest NAV': '最新净值',
    'NAV Date': '净值日期',
    'Latest Value': '最新值',
    'Value Date': '数值日期',
    'Latest Price': '最新价格',
    'Price Date': '价格日期',
    'Index Date': '指数日期',
    'Cumulative NAV Date': '复权累计净值日期',
    'Latest Quote': '最新报价',
    'Quote Date': '报价日期',
    'Selected Quotes': '选中报价',
    Quote: '报价',
    Restore: '恢复',
    Archive: '归档',
    'Asset Detail': '资产详情',
    'Instrument Summary': '标的摘要',
    'Asset ID': '资产 ID',
    'Refresh Status': '刷新状态',
    'Valuation Path': '估值路径',
    'Total Return Path': '总回报路径',
    'Quote Snapshot': '报价快照',
    'NAV Sequence': '净值序列',
    'All Shared Market Data': '全部共享市场数据',
    'No market data': '无市场数据',
    Missing: '缺失',
    'Shared market data': '共享市场数据',
    Fresh: '新鲜',
    'Raw OHLCV retained': '已保留原始行情',
    'OHLCV QFQ available': '可使用前复权行情',
    'Raw OHLCV only': '仅有原始行情',
    'OHLCV pending': '行情待更新',
    'Source update failed': '来源更新失败',
    'No New Data': '暂无新数据',
    'Price & Volume': '价格与成交量',
    'Forward-adjusted (QFQ)': '前复权（QFQ）',
    'Raw exchange price': '交易所原始价格',
    observations: '个观测值',
    'Raw OHLCV is retained in the database. QFQ is calculated for display from complete provider adjustment factors; volume remains raw.':
      '数据库保留原始行情；展示价格根据完整复权因子计算为前复权，成交量保持原始口径。',
    'Raw OHLCV is retained in the database. QFQ is unavailable because one or more bars lack a valid adjustment factor.':
      '数据库保留原始行情；因部分行情缺少有效复权因子，暂不能展示前复权价格。',
    'No canonical OHLCV bars are stored yet.': '尚未保存标准行情。',
    'Source update failed; existing canonical history was preserved.':
      '来源更新失败，已有标准历史数据已保留。',
    'QFQ price': '前复权价格',
    'raw price': '原始价格',
    'canonical close series': '标准收盘价序列',
    'Total Return Index': '全收益指数',
    'Price Index': '价格指数',
    'Index Series': '指数序列',
    'Close · Total Return': '收盘点位 · 全收益',
    'Index Level': '指数点位',
    'Index Data': '指数数据',
    'Market Data Status': '行情数据状态',
    'Provider Symbol': '提供方代码',
    Frequency: '更新频率',
    'Market Calendar': '市场日历',
    'Last Data Update': '最后数据更新',
    Series: '序列',
    'Return Semantics': '收益口径',
    'History Start': '历史起点',
    'Latest Observation': '最新观测日',
    'Source Refresh': '来源刷新',
    'Asset Data Updated': '资产数据更新时间',
    'Since inception': '成立以来',
    'Ann. Volatility': '年化波动率',
    'Metrics Matrix': '指标矩阵',
    'Monthly Return Matrix': '月度收益矩阵',
    Metric: '指标',
    'Period Return': '区间收益',
    'Ann. Return': '年化收益',
    'Sharpe Ratio': '夏普比率',
    'Sortino Ratio': '索提诺比率',
    'Calmar Ratio': '卡玛比率',
    'Max DD': '最大回撤',
    'Recovery Days': '修复天数',
    Unrecovered: '尚未修复',
    Year: '年份',
    'Yearly / YTD': '全年 / 年初至今',
    Close: '收盘价',
    'Daily Change': '日涨跌',
    Volume: '成交量',
    Turnover: '成交额',
    '1 Month': '1 个月',
    '3 Months': '3 个月',
    '6 Months': '6 个月',
    '1 Year': '1 年',
    'Available History': '全部可用历史',
    'Growth of Price': '价格增长',
    'Recent Daily Bars': '近期日行情',
    High: '最高价',
    Low: '最低价',
    Change: '涨跌',
    'Annualized Volatility': '年化波动率',
    'Maximum Drawdown': '最大回撤',
    'Current Drawdown': '当前回撤',
    'Underlying Terms': '挂钩标的',
    'Current Risk': '当前风险',
    'Annual Coupon': '年化票息',
    Strike: '执行价',
    Initial: '初始价',
    KI: '敲入价',
    KO: '敲出价',
    Deliverable: '可接票',
    'Cash settled': '现金结算',
    'Delivery buffer': '距接票价',
    'Strike distance': '距执行价',
    'delivery strike': '接票价',
    'above delivery strike': '高于接票价',
    'below delivery strike': '低于接票价',
    'Spot / delivery strike - 1. This monitors current price distance and does not confirm a delivery event.':
      '现价 / 接票价 - 1。该指标仅监控当前价格距离，不代表已经确认接票。',
    Observations: '观测数',
    'Trailing Returns · Standardized Engine': '滚动区间收益 · 标准化引擎',
    'Annual Returns · Standardized Engine': '年度收益 · 标准化引擎',
    'Risk Detail · Standardized Engine': '风险明细 · 标准化引擎',
    'Standardized Risk Metrics': '标准化风险指标',
  },
}

const basePatterns: LanguagePatternMessages = {
  'zh-Hans': [
    { match: /^Open (.+)$/, replace: (name) => `打开 ${name}` },
    { match: /^(.+) Watchlists · (.+) Securities$/, replace: (watchlists, securities) => `${watchlists} 个关注列表 · ${securities} 只证券` },
    { match: /^Created watchlist "(.+)"\.$/, replace: (name) => `已创建关注列表“${name}”。` },
    { match: /^Copied watchlist "(.+)"\.$/, replace: (name) => `已复制关注列表“${name}”。` },
    { match: /^Deleted watchlist "(.+)"\.$/, replace: (name) => `已删除关注列表“${name}”。` },
    { match: /^Reorder (.+)$/, replace: (name) => `调整 ${name} 顺序` },
    { match: /^Copied portfolio "(.+)"\.$/, replace: (name) => `已复制组合“${name}”。` },
    { match: /^Deleted portfolio "(.+)"\.$/, replace: (name) => `已删除组合“${name}”。` },
    { match: /^As of (.+)$/, replace: (date) => `截至 ${date}` },
    { match: /^(.+) above delivery strike$/, replace: (distance) => `${distance} 高于接票价` },
    { match: /^(.+) below delivery strike$/, replace: (distance) => `${distance} 低于接票价` },
    { match: /^(.+) selected quotes?$/, replace: (count) => `${count} 个选中报价` },
    { match: /^Latest NAV (.+)$/, replace: (date) => `最新净值 ${date}` },
    { match: /^Latest Quote (.+)$/, replace: (date) => `最新报价 ${date}` },
    { match: /^(.+) High$/, replace: (range) => `${periodLabel(range)}区间最高` },
    { match: /^(.+) Low$/, replace: (range) => `${periodLabel(range)}区间最低` },
    { match: /^Standardized · as of (.+)$/, replace: (date) => `标准化口径 · 截至 ${date}` },
    { match: /^Cumulative return from (.+)$/, replace: (basis) => `基于${basis}的累计收益` },
    { match: /^Total (.+)$/, replace: (count) => `总计 ${count}` },
    { match: /^Active (.+)$/, replace: (count) => `启用 ${count}` },
    { match: /^Archived (.+)$/, replace: (count) => `归档 ${count}` },
    { match: /^Funds (.+)$/, replace: (count) => `基金 ${count}` },
    { match: /^Funds Missing NAV (.+)$/, replace: (count) => `缺少净值基金 ${count}` },
    { match: /^Funds Missing Quote (.+)$/, replace: (count) => `缺少报价基金 ${count}` },
    { match: /^Visible (.+)$/, replace: (count) => `可见 ${count}` },
    { match: /^Selected asset: (.+)$/, replace: (asset) => `已选资产：${asset}` },
    { match: /^Showing (.+) recent rows$/, replace: (count) => `显示最近 ${count} 行` },
    { match: /^Showing (.+) recent points$/, replace: (count) => `显示最近 ${count} 个点` },
    { match: /^File ready: (.+)$/, replace: (file) => `文件已就绪：${file}` },
    { match: /^(.+) rows ready to import$/, replace: (count) => `${count} 行可导入` },
    { match: /^Parsed from (.+)$/, replace: (source) => `解析来源：${source}` },
    { match: /^Updated (.+) to (.+)\.$/, replace: (pair, rate) => `已将 ${pair} 更新为 ${rate}。` },
    { match: /^Updated (.+) for "(.+)"\.$/, replace: (basis, name) => `已更新“${name}”的${basis}。` },
    { match: /^Created instrument "(.+)"\.$/, replace: (name) => `已创建标的“${name}”。` },
    { match: /^Saved shared source settings for "(.+)"\.$/, replace: (name) => `已保存“${name}”的共享来源设置。` },
    { match: /^Triggered refresh for "(.+)"\.$/, replace: (name) => `已触发“${name}”刷新。` },
    { match: /^Imported NAV history for "(.+)"\.$/, replace: (name) => `已导入“${name}”的净值历史。` },
    { match: /^Archived "(.+)"\. Downstream search now hides it by default\.$/, replace: (name) => `已归档“${name}”。下游搜索默认隐藏该标的。` },
    { match: /^Restored "(.+)" to downstream search\.$/, replace: (name) => `已恢复“${name}”到下游搜索。` },
  ],
}

const LanguageContext = createContext<LanguageContextValue | null>(null)

const systemLabelLookup = Object.fromEntries(
  Object.entries({ ...systemMessages, ...baseMessages['zh-Hans'] }).map(([key, value]) => [key.toLowerCase(), value]),
)
const systemEnglishLabels = Object.fromEntries(
  Object.entries({ ...systemMessages, ...baseMessages['zh-Hans'] }).map(([en, zh]) => [zh, en]),
)
const systemLabel = (value: string): string => resolveTranslation(
  value, 'zh-Hans', { 'zh-Hans': systemLabelLookup },
  { 'zh-Hans': [...systemPatterns, ...(basePatterns['zh-Hans'] || [])] },
)
const periodLabel = (value: string): string => {
  const period = value.toUpperCase().match(/^(\d+)([DWMY])$/)
  if (!period) return systemLabel(value)
  const unit = period[2] === 'D' ? '天' : period[2] === 'W' ? '周' : period[2] === 'M' ? '个月' : '年'
  return `${period[1]} ${unit}`
}

// System-field search accepts both display languages while leaving field keys unchanged.
export function matchesSystemLabel(label: string, query: string) {
  const search = query.trim().toLowerCase()
  return [label, systemSourceAliases[label] || '', systemEnglishLabels[label] || '', systemLabel(label)]
    .some((value) => value.toLowerCase().includes(search))
}

const systemPatterns: LanguagePattern[] = [
  { match: /^Benchmark comparison is unavailable because required observations are missing: (.+)$/, replace: (dates) => `基准缺少必要日期的价格，暂时无法比较：${dates.replace('No official market calendar is available to confirm closures.', systemLabel('No official market calendar is available to confirm closures.'))}` },
  { match: /^Benchmark comparison is unavailable because (\d+) eligible portfolio return dates are missing from benchmark history$/, replace: (count) => `基准历史缺少 ${count} 个组合有效收益日期，暂时无法比较` },
  { match: /^confirmed (total|price)-return basis \(([^)]+)\)$/, replace: (kind, basis) => `已确认的${kind === 'total' ? '总回报' : '价格回报'}口径（${systemLabel(basis)}）` },
  { match: /^Required market data missing: (\d{4}-\d{2}-\d{2}); (.+)\. Supply the required observation before performance can continue\.$/, replace: (date, fields) => `缺少 ${date} 的必要行情：${fields.replace(/ valuation price/g, ' 估值价格').replace(/FX ([A-Z]{3}\/[A-Z]{3})/g, '$1 汇率')}。补齐该日数据后才能继续计算绩效。` },
  { match: /^View\s*:\s*(.+)$/, replace: (name) => `视图：${systemLabel(name)}` },
  { match: /^(\d+) Portfolios(?: · (.+))?$/, replace: (count, rest) => `${count} 个组合${rest ? ` · ${systemLabel(rest)}` : ''}` },
  { match: /^([\d,]+) (accounts?|cash accounts?|holdings accounts?|open option obligations|open lots?|holdings?|securities|instruments?|instruments\/cash|transactions?|rows?|items?|issues?|contracts?|notes?|peers|days|calendar days|snapshots|groups|activities|external flows|(?:complete |paired |risk |return )?observations)\.?$/i, replace: (count, unit) => `${count} ${unit.toLowerCase() === 'observations' ? '个观测值' : systemLabel(unit)}` },
  { match: /^(Postings|Lots|Position Lots|History|Positions|Transactions|Ledger|Held|Observed|Former) (\d+)$/, replace: (label, count) => `${systemLabel(label)} ${count}` },
  { match: /^(.+) actions$/, replace: (name) => `${systemLabel(name)}操作` },
  { match: /^Move (.+) (up|down)$/, replace: (name, direction) => `${direction === 'up' ? '上移' : '下移'} ${name}` },
  { match: /^Sort by (.+)$/i, replace: (label) => `按${systemLabel(label)}排序` },
  { match: /^Sort (.+?):? (ascending|descending|no sorting)$/, replace: (label, direction) => `${systemLabel(label)}：${systemLabel(direction)}` },
  { match: /^(.+\.) (Sort .+: (?:ascending|descending|no sorting))$/, replace: (description, sort) => `${systemLabel(description)} ${systemLabel(sort)}` },
  { match: /^Resize (.+) column$/, replace: (label) => `调整${systemLabel(label)}列宽` },
  { match: /^Select (.+)$/, replace: (name) => `选择 ${name}` },
  { match: /^(.+) trend$/, replace: (label) => `${systemLabel(label)}趋势` },
  { match: /^Interactive (.+) chart$/, replace: (label) => `交互式${systemLabel(label)}图` },
  { match: /^(\d+[DWMY]|MTD|YTD) (Total Return|Return|Vol(?:atility)?|Sharpe|Max(?:imum)? Drawdown|Max DD)$/i, replace: (period, metric) => `${periodLabel(period)}${systemLabel(metric)}` },
  { match: /^(Return|Vol(?:atility)?|Sharpe|Max(?:imum)? Drawdown|Max DD) (\d+[DWMY]|MTD|YTD)$/i, replace: (metric, period) => `${periodLabel(period)}${systemLabel(metric)}` },
  { match: /^(\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2}|latest)$/, replace: (start, end) => `${start} 至 ${end === 'latest' ? '最新' : end}` },
  { match: /^Showing (\d+) of (\d+) (matched )?rows$/, replace: (shown, total, matched) => `显示 ${shown} / ${total} 行${matched ? '匹配结果' : ''}` },
  { match: /^Per-instrument metric as-of (\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})$/, replace: (start, end) => `各标的指标截至日期：${start} 至 ${end}` },
  { match: /^Metrics as of (.+)$/, replace: (date) => `指标截至 ${date}` },
  { match: /^Latest observation is (\d+) calendar days old; the (daily|weekly|monthly) freshness allowance is (\d+) days\.$/, replace: (age, frequency, allowance) => `最近观测距今 ${age} 个自然日；${frequency === 'daily' ? '日度' : frequency === 'weekly' ? '周度' : '月度'}数据允许延迟 ${allowance} 天。` },
  { match: /^Latest observation (\d{4}-\d{2}-\d{2}) is older than expected completed session (\d{4}-\d{2}-\d{2})\.$/, replace: (latest, expected) => `最近观测日期 ${latest} 早于应已完成的交易日 ${expected}。` },
  { match: /^(\d+) unavailable$/, replace: (count) => `${count} 个不可用` },
  { match: /^(.+) availability: (.+)$/, replace: (metric, reason) => `${systemLabel(metric)}可用性：${systemLabel(reason)}` },
  { match: /^Risk basis partial - (\d+) instrument\(s\) have observation gaps$/, replace: (count) => `风险计算依据不完整：${count} 个标的存在观测缺口` },
  { match: /^(Daily|Weekly|Monthly) risk basis - aligned observations$/, replace: (frequency) => `${frequency === 'Daily' ? '日度' : frequency === 'Weekly' ? '周度' : '月度'}风险口径：观测数据已对齐` },
  { match: /^(\d+[MY]) (EWMA(?: \+ Shrinkage)?|Sample Covariance)$/, replace: (period, model) => `${periodLabel(period)} ${systemLabel(model)}` },
  { match: /^([\d,]+\/[\d,]+) complete observations$/, replace: (count) => `${count} 个完整观测值` },
  { match: /^([\d,]+\/[\d,]+) complete return rows in ([^;]+)$/, replace: (count, interval) => `${count} 行完整收益记录，区间 ${interval.replace(/EOD/g, '日终')}` },
  { match: /^([\d.]+%) missing$/, replace: (percent) => `缺失 ${percent}` },
  { match: /^latest (\d{4}-\d{2}-\d{2}|—)$/, replace: (date) => `最新 ${date}` },
  { match: /^(Policy|configuration|selection) (.+)$/, replace: (label, versions) => `${systemLabel(label)} ${systemLabel(versions)}` },
  { match: /^Current rolling risk requires one period identity for the return ending (\d{4}-\d{2}-\d{2}); (.+)\.$/, replace: (date, starts) => `当前滚动风险计算要求截至 ${date} 的收益区间起点一致；各标的起点：${starts}。` },
  { match: /^Current rolling risk requires identical return dates after all active holdings have history; missing (\d+) date\(s\), beginning (.+)\.$/, replace: (count, dates) => `当前滚动风险计算要求所有在持标的历史开始后的收益日期一致；缺少 ${count} 个日期，最早为 ${dates}。` },
  { match: /^All (\d+) scope members must share one complete aligned return window; no members or dates were dropped\.?$/, replace: (count) => `范围内全部 ${count} 个成员必须具有完整且一致的收益区间；未剔除任何成员或日期。` },
  { match: /^Return ending (\d{4}-\d{2}-\d{2}) starts at (.+?); scope members do not share one period identity\.?$/, replace: (end, start) => `截至 ${end} 的收益区间始于 ${systemLabel(start)}；范围内成员的收益区间起点不一致。` },
  { match: /^Missing dates \((\d+) total\): (.+)\.?$/, replace: (count, dates) => `缺失日期（共 ${count} 个）：${dates.replace(/\.$/, '')}。` },
  { match: /^(SAA|TAA)( gap)? (-?[\d.]+%)$/, replace: (label, gap, value) => `${systemLabel(label)}${gap ? '缺口' : ''} ${value}` },
  { match: /^Net (.+)$/, replace: (value) => `净额 ${value}` },
  { match: /^latest trade (.+)$/, replace: (date) => `最近交易 ${date}` },
  { match: /^Settle via (.+)$/, replace: (name) => `通过 ${name} 结算` },
  { match: /^Position EOD (.+)$/, replace: (date) => `持仓日终确认 ${date}` },
  { match: /^Economic (\d{4}-\d{2}-\d{2})$/, replace: (date) => `经济生效 ${date}` },
  { match: /^Settled cash basis (.+), FX (.+); pending basis (.+), FX (.+)\.(?: (.+))?$/, replace: (cash, cashFx, pending, pendingFx, detail) => `已结算现金成本 ${cash}，汇兑损益 ${cashFx}；待结算成本 ${pending}，汇兑损益 ${pendingFx}。${detail ? systemLabel(detail) : ''}` },
  { match: /^Scenario (\d+) (.+)$/, replace: (count, label) => `情景 ${count} ${systemLabel(label)}` },
  { match: /^Underlying (\d+)$/, replace: (count) => `挂钩标的 ${count}` },
  { match: /^Updated (\d+) market-data points for (.+): raw close for valuation\/trading and (\d+) qfq adjusted closes for charts and total return; (\d+) raw OHLCV bars; (\d+) confirmed and (\d+) review-required share-adjustment event\(s\)\.$/, replace: (count, symbol, adjusted, bars, confirmed, review) => `已更新 ${symbol} 的 ${count} 个行情数据点：原始收盘价用于估值和交易，${adjusted} 个前复权收盘价用于图表和总回报；${bars} 条原始开高低收量行情；份额调整事件中 ${confirmed} 个已确认、${review} 个待复核。` },
  { match: /^Run analysis date (\d{4}-\d{2}-\d{2}) does not match the latest portfolio date (\d{4}-\d{2}-\d{2})\.?$/, replace: (run, latest) => `运行分析日期 ${run} 与组合最新日期 ${latest} 不一致。` },
  { match: /^(.+) Series$/, replace: (label) => `${systemLabel(label)}序列` },
  { match: /^(.+) (Adjusted Close|Close|Unit NAV|Dividend-Reinvested Total Return NAV|Spot) series$/, replace: (name, basis) => `${name} ${systemLabel(basis)}序列` },
  { match: /^Market value uses (.+)\. Return analysis uses (.+); the performance series does not replace the valuation quote\.$/, replace: (valuation, performance) => `市值采用${systemLabel(valuation)}。收益分析采用${systemLabel(performance)}；业绩序列不替代估值报价。` },
  { match: /^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d+)(?:, (\d{4}))?$/, replace: (month, day, year) => `${year ? `${year}年` : ''}${['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'].indexOf(month) + 1}月${day}日` },
  { match: /^Missing required fields: (.+)$/, replace: (fields) => `缺少必填字段：${fields.split(' / ').map(systemLabel).join(' / ')}` },
  { match: /^Eligible-sleeve normalized HHI (.+)$/, replace: (value) => `纳入计算资产归一化集中度 ${value}` },
  { match: /^Asset mix by signed portfolio weight: (.+)$/, replace: (mix) => `按有符号组合权重划分的资产构成：${mix.split(', ').map(systemLabel).join('，')}` },
  { match: /^(Securities|FCN|Options|Cash & Settlement) (-?[\d.]+%)$/, replace: (label, weight) => `${systemLabel(label)} ${weight}` },
  { match: /^(\d+) lines$/, replace: '$1 行' },
  { match: /^(\d+) (?:more notes|more)$/,  replace: (count) => `另有 ${count} 条` },
  { match: /^(.+) through (.+); each Watchlist row uses that instrument's own latest calculation date\.$/, replace: (label, date) => `${systemLabel(label)}截至 ${date}；各行使用该标的最近的计算日期。` },
  { match: /^(.+) pct$/, replace: (value) => `${value} 分位` },
  { match: /^Copied (\d+) instruments to "(.+)"\.(?: (\d+) already existed there\.)?$/, replace: (count, name, existing) => `已将 ${count} 个标的复制到“${name}”。${existing ? `其中 ${existing} 个已存在。` : ''}` },
  { match: /^Moved (\d+) instruments to "(.+)"\.(?: (\d+) already existed there\.)?$/, replace: (count, name, existing) => `已将 ${count} 个标的移动到“${name}”。${existing ? `其中 ${existing} 个已存在。` : ''}` },
  { match: /^Watchlist "(.+)" created\.$/, replace: (name) => `已创建关注列表“${name}”。` },
  { match: /^View saved as "(.+)"\.$/, replace: (name) => `视图已保存为“${name}”。` },
  { match: /^Created taxonomy "(.+)"\.$/, replace: (name) => `已创建分类体系“${name}”。` },
  { match: /^Deleted taxonomy "(.+)"\.$/, replace: (name) => `已删除分类体系“${name}”。` },
  { match: /^Default taxonomy set to "(.+)"\.$/, replace: (name) => `默认分类体系已设为“${name}”。` },
  { match: /^Added (.+) to unassigned instruments\.$/, replace: (name) => `已将 ${name} 添加到未归类标的。` },
  { match: /^Remove (.+)$/, replace: (name) => `移除 ${name}` },
  { match: /^Period (\d+[DWMY])$/, replace: (period) => periodLabel(period) },
]

const textNodeOriginals = new WeakMap<Text, string>()
const elementAttributeOriginals = new WeakMap<Element, Map<string, string>>()

function mergeMessages(messages?: LanguageMessages): LanguageMessages {
  const chinese = { ...systemMessages, ...baseMessages['zh-Hans'], ...messages?.['zh-Hans'] }
  const english = Object.fromEntries(Object.entries(chinese).map(([en, zh]) => [zh, en]))
  return supportedLanguages.reduce<LanguageMessages>((merged, language) => {
    const dictionary = language.value === 'en' ? { ...english, ...messages?.en } : chinese
    merged[language.value] = { ...Object.fromEntries(Object.entries(dictionary).map(([key, value]) => [key.toLowerCase(), value])), ...dictionary }
    return merged
  }, {})
}

function mergePatterns(patterns?: LanguagePatternMessages): LanguagePatternMessages {
  return supportedLanguages.reduce<LanguagePatternMessages>((merged, language) => {
    merged[language.value] = [
      ...(language.value === 'zh-Hans' ? systemPatterns : [
        { match: /^分类层级 (\d+)$/, replace: 'Taxonomy Level $1' },
        { match: /^(\d+) 个标的$/, replace: '$1 instruments' },
        { match: /^(\d+) 项限制$/, replace: '$1 limitations' },
        { match: /^(\d+) 项$/, replace: '$1 items' },
        { match: /^(\d+) 次$/, replace: '$1 reviews' },
        { match: /^近 (\d+) 日$/, replace: 'Last $1 days' },
        { match: /^重点标的：(.+)$/, replace: 'Focus instruments: $1' },
        { match: /^已记录 (\d+) 条查阅记录，正在整理回答…$/, replace: '$1 source records saved. Preparing a reply…' },
        { match: /^更新于 (.+)$/, replace: 'Updated $1' },
        { match: /^依据日期 (.+)$/, replace: 'Evidence date $1' },
        { match: /^发布时间 (.+)$/, replace: 'Published $1' },
        { match: /^数据 (\d{4}-\d{2}-\d{2})$/, replace: 'Data $1' },
        { match: /^跟进日期 (.+)$/, replace: 'Follow-up date $1' },
        { match: /^(.+)（时区未披露）$/, replace: '$1 (timezone not disclosed)' },
        { match: /^View\s*:\s*分类$/, replace: 'View: Classification' },
        { match: /^(.+) actions$/, replace: (name: string) => `${systemSourceAliases[name] || name} actions` },
      ]),
      ...(basePatterns[language.value] || []),
      ...(patterns?.[language.value] || []),
    ]
    return merged
  }, {})
}

function interpolate(template: string, values?: InterpolationValues) {
  if (!values) {
    return template
  }
  return template.replace(/\{(\w+)\}/g, (match, key: string) => {
    const value = values[key]
    return value == null ? match : String(value)
  })
}

function normalizeLanguage(value: string | null | undefined): SupportedLanguage | null {
  const normalized = (value || '').trim().toLowerCase().replace('_', '-')
  if (!normalized) {
    return null
  }
  if (normalized === 'zh' || normalized === 'zh-cn' || normalized === 'zh-hans' || normalized.startsWith('zh-hans-')) {
    return 'zh-Hans'
  }
  if (normalized === 'en' || normalized.startsWith('en-')) {
    return 'en'
  }
  return null
}

function getCookieValue(name: string) {
  if (typeof document === 'undefined') {
    return null
  }
  const encodedName = `${encodeURIComponent(name)}=`
  const cookie = document.cookie
    .split(';')
    .map((part) => part.trim())
    .find((part) => part.startsWith(encodedName))
  return cookie ? decodeURIComponent(cookie.slice(encodedName.length)) : null
}

function setLanguageCookie(language: SupportedLanguage) {
  if (typeof document === 'undefined') {
    return
  }
  document.cookie = `${encodeURIComponent(LANGUAGE_COOKIE_NAME)}=${encodeURIComponent(language)}; path=/; max-age=31536000; samesite=lax`
}

function detectInitialLanguage(): SupportedLanguage {
  if (typeof window === 'undefined') {
    return 'en'
  }

  const params = new URLSearchParams(window.location.search)
  const urlLanguage = normalizeLanguage(params.get('lang') || params.get('language'))
  if (urlLanguage) {
    return urlLanguage
  }

  try {
    const storedLanguage = normalizeLanguage(window.localStorage.getItem(LANGUAGE_STORAGE_KEY))
    if (storedLanguage) {
      return storedLanguage
    }
  } catch {
    // Ignore storage failures in private or locked-down browser contexts.
  }

  const cookieLanguage = normalizeLanguage(getCookieValue(LANGUAGE_COOKIE_NAME))
  if (cookieLanguage) {
    return cookieLanguage
  }

  const browserLanguages = navigator.languages?.length ? navigator.languages : [navigator.language]
  for (const browserLanguage of browserLanguages) {
    const normalized = normalizeLanguage(browserLanguage)
    if (normalized) {
      return normalized
    }
  }

  return 'en'
}

function resolveTranslation(
  source: string,
  language: SupportedLanguage,
  messages: LanguageMessages,
  patterns: LanguagePatternMessages,
): string {
  source = systemSourceAliases[source] || source
  const direct = messages[language]?.[source] || messages[language]?.[source.toLowerCase()]
    || (language === 'zh-Hans' ? messages[language]?.[`${source}.`] || messages[language]?.[`${source.toLowerCase()}.`] : undefined)
  if (direct) {
    return direct
  }

  for (const pattern of patterns[language] || []) {
    const match = source.match(pattern.match)
    if (!match) {
      continue
    }
    if (typeof pattern.replace === 'string') {
      return source.replace(pattern.match, pattern.replace)
    }
    return pattern.replace(...match.slice(1))
  }

  // Translate fixed UI fragments while retaining the values between them.
  const fragment = source.match(/^([;|·]\s*)?(.+?)(\s*[:：(·])?$/)
  if (fragment && (fragment[1] || fragment[3])) {
    const inner = resolveTranslation(fragment[2], language, messages, patterns)
    if (inner !== fragment[2]) return `${fragment[1] || ''}${inner}${fragment[3] || ''}`
  }
  for (const separator of [' · ', '. ', ': ', '; ', ' / ']) {
    if (separator === ': ') {
      const labeledValue = source.match(/^(.+?): (.+)$/)
      if (labeledValue) {
        const label = resolveTranslation(labeledValue[1], language, messages, patterns)
        const value = resolveTranslation(labeledValue[2], language, messages, patterns)
        if (label !== labeledValue[1] || value !== labeledValue[2]) return `${label}${language === 'en' ? ': ' : '：'}${value}`
      }
      continue
    }
    if (source.includes(separator)) {
      const parts = source.split(separator)
      const translated = parts.map((part) => resolveTranslation(part, language, messages, patterns))
      if (parts.some((part, index) => part !== translated[index])) {
        const joiner = language === 'zh-Hans' && separator === '; ' ? '；'
          : language === 'zh-Hans' && separator === '. ' ? '。' : separator
        return translated.map((part) => separator === '. ' ? part.replace(/[。.]$/, '') : part).join(joiner)
      }
    }
  }
  if (source.endsWith('.')) {
    const sentence = resolveTranslation(source.slice(0, -1), language, messages, patterns)
    if (sentence !== source.slice(0, -1)) return `${sentence}${language === 'en' ? '.' : '。'}`
  }

  return source
}

function translateText(
  source: string,
  language: SupportedLanguage,
  messages: LanguageMessages,
  patterns: LanguagePatternMessages,
) {
  const leading = source.match(/^\s*/)?.[0] || ''
  const trailing = source.match(/\s*$/)?.[0] || ''
  const core = source.trim().replace(/\s+/g, ' ')
  if (!core) {
    return source
  }
  const translated = resolveTranslation(core, language, messages, patterns)
  return `${leading}${translated}${trailing}`
}

function isKnownRenderedTranslation(
  value: string,
  original: string,
  messages: LanguageMessages,
  patterns: LanguagePatternMessages,
) {
  return supportedLanguages.some((language) => value === translateText(original, language.value, messages, patterns))
}

function contextualDomText(node: Text, original: string) {
  if (/^\d+[DWMY]$/.test(original.trim()) && node.parentElement?.closest('button, option, th')) {
    return `Period ${original.trim()}`
  }
  if (node.parentElement?.closest('.investment-research-importance') && /^(High|Low)$/i.test(original.trim())) {
    return `${original.trim()} importance`
  }
  if (original.trim() !== 'Close' || !node.parentElement?.closest('button')) {
    return original
  }
  const leading = original.match(/^\s*/)?.[0] || ''
  const trailing = original.match(/\s*$/)?.[0] || ''
  return `${leading}Close Action${trailing}`
}

function isKnownRenderedTextTranslation(
  node: Text,
  value: string,
  original: string,
  messages: LanguageMessages,
  patterns: LanguagePatternMessages,
) {
  const contextualOriginal = contextualDomText(node, original)
  return supportedLanguages.some((language) =>
    value === (
      translateText(language.value === 'en' ? original : contextualOriginal, language.value, messages, patterns)
    ),
  )
}

function shouldIgnoreElement(element: Element | null) {
  if (!element) {
    return false
  }
  return Boolean(
    element.closest(
      'script, style, code, pre, textarea, [contenteditable="true"], [translate="no"], [data-investment-studio-i18n-ignore="true"]',
    ),
  )
}

function translateTextNode(
  node: Text,
  language: SupportedLanguage,
  messages: LanguageMessages,
  patterns: LanguagePatternMessages,
) {
  if (shouldIgnoreElement(node.parentElement)) {
    return
  }

  const storedOriginal = textNodeOriginals.get(node)
  let original = storedOriginal || node.data
  if (!storedOriginal) {
    textNodeOriginals.set(node, original)
  } else if (!isKnownRenderedTextTranslation(node, node.data, storedOriginal, messages, patterns)) {
    original = node.data
    textNodeOriginals.set(node, original)
  }

  const next = translateText(language === 'en' ? original : contextualDomText(node, original), language, messages, patterns)
  if (node.data !== next) {
    node.data = next
  }
}

function translateElementAttributes(
  element: Element,
  language: SupportedLanguage,
  messages: LanguageMessages,
  patterns: LanguagePatternMessages,
) {
  if (element instanceof HTMLAnchorElement && element.hasAttribute('data-workspace-link')) {
    const nextHref = withLanguage(element.getAttribute('href') || '/', language)
    if (element.getAttribute('href') !== nextHref) element.setAttribute('href', nextHref)
  }
  if (shouldIgnoreElement(element)) {
    return
  }

  const translatableAttributes = ['aria-label', 'aria-description', 'placeholder', 'title']
  for (const attribute of translatableAttributes) {
    const currentValue = element.getAttribute(attribute)
    if (!currentValue) {
      continue
    }
    let originals = elementAttributeOriginals.get(element)
    if (!originals) {
      originals = new Map<string, string>()
      elementAttributeOriginals.set(element, originals)
    }
    const storedOriginal = originals.get(attribute)
    let original = storedOriginal || currentValue
    if (!storedOriginal) {
      originals.set(attribute, original)
    } else if (!isKnownRenderedTranslation(currentValue, storedOriginal, messages, patterns)) {
      original = currentValue
      originals.set(attribute, original)
    }
    const next = translateText(original, language, messages, patterns)
    if (currentValue !== next) {
      element.setAttribute(attribute, next)
    }
  }
}

function translateDomTree(
  root: ParentNode,
  language: SupportedLanguage,
  messages: LanguageMessages,
  patterns: LanguagePatternMessages,
) {
  if (root instanceof Element) {
    translateElementAttributes(root, language, messages, patterns)
  }

  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT)
  let current = walker.nextNode()
  while (current) {
    if (current.nodeType === Node.TEXT_NODE) {
      translateTextNode(current as Text, language, messages, patterns)
    } else if (current.nodeType === Node.ELEMENT_NODE) {
      translateElementAttributes(current as Element, language, messages, patterns)
    }
    current = walker.nextNode()
  }
}

export function LanguageProvider({
  children,
  messages,
  patterns,
  enableDomTranslation = true,
}: LanguageProviderProps) {
  const [language, setLanguageState] = useState<SupportedLanguage>(() => detectInitialLanguage())
  const mergedMessages = useMemo(() => mergeMessages(messages), [messages])
  const mergedPatterns = useMemo(() => mergePatterns(patterns), [patterns])

  const setLanguage = useCallback((nextLanguage: SupportedLanguage) => {
    const url = new URL(window.location.href)
    if (!url.searchParams.has('next') && (url.searchParams.has('lang') || url.searchParams.has('language'))) {
      url.searchParams.delete('language')
      url.searchParams.set('lang', nextLanguage)
      window.history.replaceState(window.history.state, '', url)
    }
    setLanguageState(nextLanguage)
  }, [])

  const t = useCallback(
    (message: string, values?: InterpolationValues) =>
      interpolate(resolveTranslation(message, language, mergedMessages, mergedPatterns), values),
    [language, mergedMessages, mergedPatterns],
  )

  useEffect(() => {
    const languageDefinition = supportedLanguages.find((item) => item.value === language)
    document.documentElement.lang = languageDefinition?.htmlLang || language
    document.documentElement.dataset.language = language
    try {
      window.localStorage.setItem(LANGUAGE_STORAGE_KEY, language)
    } catch {
      // Ignore storage failures in private or locked-down browser contexts.
    }
    setLanguageCookie(language)
  }, [language])

  useEffect(() => {
    if (!enableDomTranslation || typeof document === 'undefined') {
      return undefined
    }

    translateDomTree(document.body, language, mergedMessages, mergedPatterns)
    const title = document.querySelector('title')
    if (title) translateDomTree(title, language, mergedMessages, mergedPatterns)

    const options = {
      attributes: true,
      attributeFilter: ['aria-label', 'aria-description', 'placeholder', 'title', 'href'],
      characterData: true,
      childList: true,
      subtree: true,
    }

    const observer = new MutationObserver((mutations) => {
      observer.disconnect()
      for (const mutation of mutations) {
        if (mutation.type === 'characterData' && mutation.target.nodeType === Node.TEXT_NODE) {
          translateTextNode(mutation.target as Text, language, mergedMessages, mergedPatterns)
        }
        if (mutation.type === 'attributes' && mutation.target.nodeType === Node.ELEMENT_NODE) {
          translateElementAttributes(mutation.target as Element, language, mergedMessages, mergedPatterns)
        }
        mutation.addedNodes.forEach((node) => {
          if (node.nodeType === Node.TEXT_NODE) {
            translateTextNode(node as Text, language, mergedMessages, mergedPatterns)
          }
          if (node.nodeType === Node.ELEMENT_NODE) {
            translateDomTree(node as Element, language, mergedMessages, mergedPatterns)
          }
        })
      }
      observer.observe(document.body, options)
    })

    observer.observe(document.body, options)

    return () => observer.disconnect()
  }, [enableDomTranslation, language, mergedMessages, mergedPatterns])

  const value = useMemo<LanguageContextValue>(
    () => ({
      language,
      setLanguage,
      t,
    }),
    [language, setLanguage, t],
  )

  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>
}

export function useLanguage() {
  const value = useContext(LanguageContext)
  if (!value) {
    throw new Error('useLanguage must be used inside LanguageProvider.')
  }
  return value
}

export function LanguageSelector() {
  const { language, setLanguage, t } = useLanguage()

  return (
    <label className="language-switcher" data-investment-studio-i18n-ignore="true">
      <span>{t('Language')}</span>
      <select
        value={language}
        onChange={(event) => setLanguage(event.target.value as SupportedLanguage)}
        aria-label={t('Language')}
      >
        {supportedLanguages.map((item) => (
          <option key={item.value} value={item.value}>
            {item.label}
          </option>
        ))}
      </select>
    </label>
  )
}
