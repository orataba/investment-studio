import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'

export const LANGUAGE_STORAGE_KEY = 'yungu.language'
export const LANGUAGE_COOKIE_NAME = 'yungu_language'

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
    Platform: '平台',
    'Database Dashboard': '数据库面板',
    API: 'API',
    Open: '打开',
    Search: '搜索',
    Type: '类型',
    Coverage: '覆盖',
    Source: '来源',
    Sort: '排序',
    Actions: '操作',
    Status: '状态',
    'Coverage Status': '状态',
    'Research lifecycle status such as watch, proposed, invested, paused, or exited.':
      '投研生命周期状态，例如观察、拟投、在投、暂停或退出。',
    Watch: '观察',
    Proposed: '拟投',
    Invested: '在投',
    Paused: '暂停',
    Exited: '退出',
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
    Basis: '基准',
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
    'Official NAV': '单位净值',
    'Total Return NAV': '累计净值',
    Overview: '总览',
    Holdings: '持仓',
    Performance: '业绩',
    Risk: '风险',
    Price: '费用',
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
    'Platform is the entry. Database Dashboard owns shared data operations.':
      '平台是入口。数据库面板负责共享数据运营。',
    'Platform currently exposes three entry points: Database Dashboard, Watchlist, and Portfolio. Shared instruments, FX, email refresh rules, and NAV imports belong to Database Dashboard so the app workflows stay decoupled.':
      '平台目前提供三个入口：数据库面板、关注列表和组合。共享标的、汇率、邮件刷新规则和净值导入归数据库面板管理，从而让各业务应用保持解耦。',
    'Registry source:': '注册表来源：',
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
    'Registry Overview': '注册表总览',
    Total: '总计',
    Funds: '基金',
    'Funds With NAV': '有净值基金',
    'Find Assets': '查找资产',
    'Code / name / asset id / identifier': '代码 / 名称 / 资产 ID / 标识',
    'All Types': '全部类型',
    'All Coverage': '全部覆盖',
    'All Sources': '全部来源',
    'All Assets': '全部资产',
    'Has NAV': '已有净值',
    'Missing NAV': '缺少净值',
    'Latest NAV Date': '最新净值日期',
    'Funds Only': '仅基金',
    'Reset Filters': '重置筛选',
    'No NAV loaded yet': '尚未加载净值',
    'Not NAV-based': '非净值型',
    'No selected quotes': '无选中报价',
    'Add Quote': '添加报价',
    'Import NAV': '导入净值',
    Refresh: '刷新',
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
    'Taxonomy Settings': '分类设置',
    'Classification Path': '分类路径',
    'Current Path': '当前路径',
    Regime: '体系',
    Unclassify: '取消分类',
    Unclassified: '未分类',
    'Stop here': '停在此级',
    'Select parent first': '请先选择上一级',
    Save: '保存',
    Saving: '保存中',
    Cancel: '取消',
    'NAV with Dividends': '累计净值',
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
    'Instrument Registry': '标的注册表',
    Include: '包含',
    'Include Archived': '包含归档',
    'Show Active Only': '仅显示启用',
    'Latest NAV': '最新净值',
    'NAV Date': '净值日期',
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
    'Platform search default': '平台默认搜索',
  },
}

const basePatterns: LanguagePatternMessages = {
  'zh-Hans': [
    { match: /^Open (.+)$/, replace: (name) => `打开 ${name}` },
    { match: /^Registry source: (.+)$/, replace: (source) => `注册表来源：${source}` },
    { match: /^(.+) Watchlists · (.+) Securities$/, replace: (watchlists, securities) => `${watchlists} 个关注列表 · ${securities} 只证券` },
    { match: /^Created watchlist "(.+)"\.$/, replace: (name) => `已创建关注列表“${name}”。` },
    { match: /^Copied watchlist "(.+)"\.$/, replace: (name) => `已复制关注列表“${name}”。` },
    { match: /^Deleted watchlist "(.+)"\.$/, replace: (name) => `已删除关注列表“${name}”。` },
    { match: /^Reorder (.+)$/, replace: (name) => `调整 ${name} 顺序` },
    { match: /^Copied portfolio "(.+)"\.$/, replace: (name) => `已复制组合“${name}”。` },
    { match: /^Deleted portfolio "(.+)"\.$/, replace: (name) => `已删除组合“${name}”。` },
    { match: /^As of (.+)$/, replace: (date) => `截至 ${date}` },
    { match: /^(.+) selected quotes?$/, replace: (count) => `${count} 个选中报价` },
    { match: /^Latest NAV (.+)$/, replace: (date) => `最新净值 ${date}` },
    { match: /^Total (.+)$/, replace: (count) => `总计 ${count}` },
    { match: /^Active (.+)$/, replace: (count) => `启用 ${count}` },
    { match: /^Archived (.+)$/, replace: (count) => `归档 ${count}` },
    { match: /^Funds (.+)$/, replace: (count) => `基金 ${count}` },
    { match: /^Funds Missing NAV (.+)$/, replace: (count) => `缺少净值基金 ${count}` },
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

const textNodeOriginals = new WeakMap<Text, string>()
const elementAttributeOriginals = new WeakMap<Element, Map<string, string>>()

function mergeMessages(messages?: LanguageMessages): LanguageMessages {
  return supportedLanguages.reduce<LanguageMessages>((merged, language) => {
    merged[language.value] = {
      ...(baseMessages[language.value] || {}),
      ...(messages?.[language.value] || {}),
    }
    return merged
  }, {})
}

function mergePatterns(patterns?: LanguagePatternMessages): LanguagePatternMessages {
  return supportedLanguages.reduce<LanguagePatternMessages>((merged, language) => {
    merged[language.value] = [
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
) {
  if (language === 'en') {
    return source
  }

  const direct = messages[language]?.[source]
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

function shouldIgnoreElement(element: Element | null) {
  if (!element) {
    return false
  }
  return Boolean(
    element.closest(
      'script, style, code, pre, textarea, [contenteditable="true"], [data-yungu-i18n-ignore="true"]',
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
  } else if (!isKnownRenderedTranslation(node.data, storedOriginal, messages, patterns)) {
    original = node.data
    textNodeOriginals.set(node, original)
  }

  const next = language === 'en' ? original : translateText(original, language, messages, patterns)
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
    const next = language === 'en' ? original : translateText(original, language, messages, patterns)
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
  const translatingRef = useRef(false)

  const setLanguage = useCallback((nextLanguage: SupportedLanguage) => {
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

    const applyTranslation = (root: ParentNode = document.body) => {
      translatingRef.current = true
      translateDomTree(root, language, mergedMessages, mergedPatterns)
      window.setTimeout(() => {
        translatingRef.current = false
      }, 0)
    }

    applyTranslation()

    const observer = new MutationObserver((mutations) => {
      if (translatingRef.current) {
        return
      }
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
    })

    observer.observe(document.body, {
      attributes: true,
      attributeFilter: ['aria-label', 'aria-description', 'placeholder', 'title'],
      characterData: true,
      childList: true,
      subtree: true,
    })

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
    <label className="language-switcher" data-yungu-i18n-ignore="true">
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
