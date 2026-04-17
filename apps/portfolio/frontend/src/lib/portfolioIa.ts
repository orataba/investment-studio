export type PortfolioCoreBlock = {
  title: string
  owner: string
  role: string
}

export type PortfolioDrillDown = {
  source: string
  target: string
}

export type PortfolioPageDefinition = {
  key: string
  label: string
  href: string
  family: 'Current-State' | 'Period' | 'Ledger' | 'Config'
  toolbarLabel: string
  summary: string
  primaryQuestion: string
  primaryObjects: string[]
  sharedContext: string[]
  coreBlocks: PortfolioCoreBlock[]
  comparator: string[]
  conditionalBlocks: string[]
  drillDowns: PortfolioDrillDown[]
  notThisPage: string[]
}

export const workspacePrimaryNavigation = [
  { label: 'Snapshot', href: '/snapshot' },
  { label: 'Holdings', href: '/holdings' },
  { label: 'Performance', href: '/performance' },
  { label: 'Risk', href: '/risk' },
  { label: 'Transactions', href: '/transactions' },
  { label: 'Accounts', href: '/accounts' },
  { label: 'Review', href: '/review' },
  { label: 'Research', href: '/research' },
  { label: 'Taxonomies', href: '/taxonomies' },
] as const

export const portfolioPageDefinitions: Record<string, PortfolioPageDefinition> = {
  snapshot: {
    key: 'snapshot',
    label: 'Snapshot',
    href: '/snapshot',
    family: 'Current-State',
    toolbarLabel: 'Page: Snapshot Report',
    summary:
      'Snapshot is the current-state report page. It answers how the portfolio looks now, what the key exposures are, and which benchmark or risk highlights need immediate explanation.',
    primaryQuestion: '这个组合现在整体怎样？',
    primaryObjects: ['PortfolioSnapshot', 'alert summary', 'selected benchmark summary'],
    sharedContext: [
      'portfolio_id: current workspace portfolio',
      'as_of_date: latest complete as_of_date',
      'resolved_primary_benchmark_assignment_id: resolved active assignment at as_of_date',
      'base_currency: portfolio base currency',
      'coverage_state: complete / partial / unavailable',
    ],
    coreBlocks: [
      { title: 'Portfolio summary hero', owner: 'PortfolioSnapshot', role: 'Show NAV, day move, status, and latest complete as_of_date' },
      { title: 'Current composition summary', owner: 'PortfolioSnapshot', role: 'Summarize asset mix, taxonomy cuts, and high-level structure' },
      { title: 'Top holdings / top groups', owner: 'PortfolioSnapshot', role: 'Surface the names or nodes driving current composition' },
      { title: 'Benchmark-relative summary', owner: 'Resolved benchmark', role: 'Show current-state deviations vs primary benchmark' },
      { title: 'Risk highlights', owner: 'RiskSnapshot summary', role: 'Lift current risk issues into a compact report layer' },
      { title: 'Alert highlights', owner: 'AlertEvent summary', role: 'Call out unresolved breaches and stale inputs' },
    ],
    comparator: ['Default comparator: primary benchmark', 'Target-related content stays summary-only and links out to Risk'],
    conditionalBlocks: ['Target drift does not become a full analysis block here', 'Risk monitoring remains a drill-down, not the main canvas'],
    drillDowns: [
      { source: 'Composition block', target: 'Holdings grouped view' },
      { source: 'Benchmark-relative summary', target: 'Performance or Risk' },
      { source: 'Risk highlights', target: 'Risk' },
      { source: 'Alert highlights', target: 'Risk / Limits & Alerts' },
    ],
    notThisPage: ['Not a full risk cockpit', 'Not a period performance page', 'Not the canonical holdings table'],
  },
  holdings: {
    key: 'holdings',
    label: 'Holdings',
    href: '/holdings',
    family: 'Current-State',
    toolbarLabel: 'Page: Statement of Assets',
    summary:
      'Holdings is the canonical current-state table. It owns positions, lots, grouped exposures, and security-detail entry, but does not absorb transactions or target analysis.',
    primaryQuestion: '当前持有什么？',
    primaryObjects: ['positions', 'lots', 'grouped exposures'],
    sharedContext: [
      'portfolio_id: current workspace portfolio',
      'as_of_date: latest complete as_of_date',
      'selected_taxonomy_id: page-specific default',
      'resolved_primary_benchmark_assignment_id: resolved active assignment at as_of_date',
      'base_currency: portfolio base currency',
      'coverage_state: complete / partial / unavailable',
    ],
    coreBlocks: [
      { title: 'Canonical holdings table', owner: 'Positions + lots', role: 'Show market value, weight, cost basis, and unrealized state' },
      { title: 'Group by taxonomy / planning axis / account', owner: 'Taxonomy + account context', role: 'Switch between portfolio aggregation lenses' },
      { title: 'Sortable and filterable columns', owner: 'Holdings workspace', role: 'Support dense table workflows without route sprawl' },
      { title: 'Relative-to-benchmark columns', owner: 'Resolved benchmark', role: 'Expose benchmark-relative composition when supported' },
      { title: 'Security detail pane entry', owner: 'Security detail surface', role: 'Open quotes, transactions, trades, events, and data quality' },
    ],
    comparator: ['Default comparator: primary benchmark', 'Target gap is not the canonical comparator on this page'],
    conditionalBlocks: ['If benchmark only has return_only capability, composition-relative columns become unavailable', 'Grouping by account should drill toward Accounts rather than replace it'],
    drillDowns: [
      { source: 'Security row', target: 'Security detail pane' },
      { source: 'Taxonomy group', target: 'Grouped holdings view' },
      { source: 'Relative columns', target: 'Performance or Risk' },
      { source: 'Account grouping', target: 'Accounts' },
    ],
    notThisPage: ['Does not replace Transactions', 'Does not own full drift or target-risk-share analysis'],
  },
  performance: {
    key: 'performance',
    label: 'Performance',
    href: '/performance',
    family: 'Period',
    toolbarLabel: 'Page: Performance',
    summary:
      'Performance is a period page. It owns return scorecards, benchmark-relative analytics, and attribution, with explicit handling for mixed benchmark timelines.',
    primaryQuestion: '这段时间赚了多少，怎么赚的？',
    primaryObjects: ['PerformanceSnapshot', 'AttributionReport', 'benchmark-relative analytics'],
    sharedContext: [
      'portfolio_id: current workspace portfolio',
      'period: user-selected period_start / period_end',
      'benchmark_resolution_mode: resolved primary benchmark over selected period',
      'selected_taxonomy_id: page-specific default',
      'base_currency: portfolio base currency',
      'coverage_state: complete / partial / unavailable',
    ],
    coreBlocks: [
      { title: 'Return scorecard', owner: 'PerformanceSnapshot', role: 'Show TTWROR, IRR, drawdown, and absolute result' },
      { title: 'Benchmark comparison', owner: 'Resolved benchmark timeline', role: 'Explain excess return and benchmark-relative context' },
      { title: 'Drawdown summary', owner: 'PerformanceSnapshot', role: 'Expose major period drawdowns and recovery status' },
      { title: 'Contribution / attribution tables', owner: 'AttributionReport', role: 'Break down result drivers by taxonomy or default planning axis' },
      { title: 'Benchmark-relative contribution', owner: 'AttributionReport + benchmark', role: 'Show active contribution when benchmark composition is available' },
    ],
    comparator: ['Default comparator: primary benchmark', 'Mixed benchmark periods must be explicit and explained via resolved timeline'],
    conditionalBlocks: ['Brinson-style attribution only appears when benchmark composition is available', 'When benchmark is return_only, degrade to performance-relative analytics only'],
    drillDowns: [
      { source: 'Attribution group', target: 'Filtered holdings or grouped contribution view' },
      { source: 'Drawdown point', target: 'Period focus view' },
      { source: 'Benchmark-relative block', target: 'Benchmark coverage detail' },
    ],
    notThisPage: ['Not the current-state holdings table', 'Does not host alert configuration'],
  },
  risk: {
    key: 'risk',
    label: 'Risk',
    href: '/risk',
    family: 'Current-State',
    toolbarLabel: 'Page: Risk',
    summary:
      'Risk is a first-class current-state workspace. It owns exposure, drift, risk-budget gaps, scenarios, and limits or alerts with explicit target-resolution rules.',
    primaryQuestion: '风险在哪里、偏离是否过大、该怎么解释？',
    primaryObjects: ['RiskSnapshot', 'AlertEvent', 'AlertRule', 'resolved TargetSet'],
    sharedContext: [
      'portfolio_id: current workspace portfolio',
      'as_of_date: latest complete as_of_date',
      'selected_taxonomy_id: planning-aware default',
      'target_resolution_mode: resolve weight and risk_budget dimensions independently',
      'resolved_primary_benchmark_assignment_id: resolved active assignment at as_of_date',
      'coverage_state: complete / partial / unavailable',
    ],
    coreBlocks: [
      { title: 'Exposure', owner: 'RiskSnapshot', role: 'Show current exposure by taxonomy, sleeve, or benchmark-aware grouping' },
      { title: 'Target weight drift', owner: 'Resolved TargetSet', role: 'Compare actual weights against resolved planning targets' },
      { title: 'Target risk budget gap', owner: 'Resolved TargetSet + risk share', role: 'Compare realized risk share against target risk budgets' },
      { title: 'Concentration and realized risk', owner: 'RiskSnapshot', role: 'Surface HHI, top-k concentration, realized risk, and tracking context' },
      { title: 'Scenario results', owner: 'RiskSnapshot + scenarios', role: 'Show scenario P&L and impacted positions' },
      { title: 'Limits & alerts', owner: 'AlertEvent + AlertRule', role: 'Own the live breach and monitoring surface' },
    ],
    comparator: ['Target drift compares to resolved TargetSet', 'Benchmark-relative risk only appears when benchmark composition is available'],
    conditionalBlocks: ['If weight dimension is not configured, drift block is hidden and marked not configured', 'If risk_budget dimension is not configured, risk budget block is hidden and marked not configured'],
    drillDowns: [
      { source: 'Drift line', target: 'Filtered Holdings' },
      { source: 'Risk budget gap line', target: 'Grouped holdings with scenario context' },
      { source: 'Alert event', target: 'Related holdings, accounts, or transactions' },
      { source: 'Scenario result', target: 'Affected positions' },
    ],
    notThisPage: ['Does not produce the formal Review narrative', 'Does not own the raw transaction ledger'],
  },
  transactions: {
    key: 'transactions',
    label: 'Transactions',
    href: '/transactions',
    family: 'Ledger',
    toolbarLabel: 'Page: Transaction Ledger',
    summary:
      'Transactions is the canonical portfolio-level fact ledger. It owns buys, sells, cash flows, fees, taxes, and transfers rather than derived analytics.',
    primaryQuestion: '账本里发生了什么？',
    primaryObjects: ['Transaction'],
    sharedContext: [
      'portfolio_id: current workspace portfolio',
      'period filter: user-selected',
      'base_currency: portfolio base currency',
    ],
    coreBlocks: [
      { title: 'Transaction ledger table', owner: 'Transaction', role: 'Show canonical facts with sort and filter support' },
      { title: 'Filters by account / instrument / type / period', owner: 'Ledger workspace', role: 'Slice the fact ledger without changing page ownership' },
      { title: 'Direct edit / delete support', owner: 'Transaction editor', role: 'Maintain correct source facts and recalc triggers' },
      { title: 'Cash flow visibility', owner: 'Transaction ledger', role: 'Keep external and internal cash movement explicit' },
    ],
    comparator: ['No canonical comparator; this page is fact-first'],
    conditionalBlocks: ['Derived Trades remain secondary surfaces, not a separate primary page'],
    drillDowns: [
      { source: 'Transaction instrument', target: 'Holdings security detail pane' },
      { source: 'Transaction account', target: 'Accounts' },
    ],
    notThisPage: ['Not a FIFO Trades primary page', 'Not a review or analytics summary page'],
  },
  accounts: {
    key: 'accounts',
    label: 'Accounts',
    href: '/accounts',
    family: 'Ledger',
    toolbarLabel: 'Page: Accounts',
    summary:
      'Accounts is the ledger-side structural page. It explains where cash and positions live, how balances map to accounts, and how derived postings connect back to transactions.',
    primaryQuestion: '底层账户结构和归属是什么？',
    primaryObjects: ['Account', 'LedgerPosting', 'linked transactions', 'account balances / positions'],
    sharedContext: [
      'portfolio_id: current workspace portfolio',
      'base_currency: portfolio base currency',
      'coverage_state: complete / partial / unavailable',
    ],
    coreBlocks: [
      { title: 'Account list', owner: 'Account', role: 'List cash and securities accounts with role and status' },
      { title: 'Account balance summary', owner: 'Account balances', role: 'Show balances and cash location by account' },
      { title: 'Account positions', owner: 'Account positions', role: 'Explain which holdings belong to which securities account' },
      { title: 'Account-level ledger slice', owner: 'LedgerPosting', role: 'Expose derived postings generated from transactions' },
      { title: 'Default settlement cash mapping', owner: 'Account configuration', role: 'Show how securities accounts settle to cash accounts' },
      { title: 'Paired securities-to-cash view', owner: 'Account pair model', role: 'Show one cash account serving multiple securities accounts' },
    ],
    comparator: ['No default benchmark comparator; this page is structure and ledger oriented'],
    conditionalBlocks: ['Account views should drill to holdings or transactions instead of duplicating them'],
    drillDowns: [
      { source: 'Account position', target: 'Holdings' },
      { source: 'Account transaction', target: 'Transactions' },
    ],
    notThisPage: ['Does not replace portfolio-level Holdings', 'Does not carry planning target configuration'],
  },
  review: {
    key: 'review',
    label: 'Review',
    href: '/review',
    family: 'Period',
    toolbarLabel: 'Page: Review Pack',
    summary:
      'Review is the buy-side period narrative layer. It owns scorecards, commentary, actions, and export artifacts, while resolving mixed benchmark and mixed target timelines explicitly.',
    primaryQuestion: '这个周期最重要的结果和动作是什么？',
    primaryObjects: ['ReviewPack', 'PerformanceSnapshot', 'PeriodRiskSummary', 'action items'],
    sharedContext: [
      'portfolio_id: current workspace portfolio',
      'period: user-selected period_start / period_end',
      'resolved_target_timeline_id: resolved over selected period',
      'target_resolution_mode: resolved target timeline over selected period',
      'benchmark_resolution_mode: resolved benchmark over selected period',
      'coverage_state: complete / partial / unavailable',
    ],
    coreBlocks: [
      { title: 'Scorecard', owner: 'ReviewPack', role: 'Summarize the period result in one decision-oriented strip' },
      { title: 'Performance summary', owner: 'PerformanceSnapshot', role: 'Explain return and benchmark-relative outcome for the period' },
      { title: 'Risk summary', owner: 'PeriodRiskSummary', role: 'Summarize realized risk, breaches, and key exposures over the period' },
      { title: 'Target summaries', owner: 'ResolvedTargetTimeline', role: 'Summarize drift and target risk budget over the selected period' },
      { title: 'Commentary, actions, export artifacts', owner: 'ReviewPack + ActionItem + ExportArtifact', role: 'Capture narrative, next steps, and archived output' },
    ],
    comparator: ['Fixed order: absolute result -> primary benchmark -> resolved target timeline -> alert breaches'],
    conditionalBlocks: ['Mixed Targets must be explicit when target source changes or dimensions resolve differently', 'When benchmark is return_only, benchmark-relative section degrades to performance-only summary'],
    drillDowns: [
      { source: 'Performance summary', target: 'Performance with preserved period and benchmark' },
      { source: 'Risk summary', target: 'Review-local period risk detail surface' },
      { source: 'Current-state follow-up item', target: 'Risk with latest complete as_of_date' },
      { source: 'Action item', target: 'Source alert, review section, or research handoff' },
    ],
    notThisPage: ['Not the current-state snapshot page', 'Does not own research configuration'],
  },
  research: {
    key: 'research',
    label: 'Research',
    href: '/research',
    family: 'Period',
    toolbarLabel: 'Page: Research Runs',
    summary:
      'Research is the portfolio workbench for run setup, current context, run history, and artifacts. It should stay compatible with heavier solver and backtest templates without losing portfolio context.',
    primaryQuestion: '当前组合要按什么 planning 语境做研究，已经跑过哪些结果，下一步该 handoff 什么？',
    primaryObjects: ['ResearchSettings', 'ResearchRun', 'ResearchArtifact'],
    sharedContext: ['portfolio_id: current workspace portfolio', 'current default planning taxonomy', 'current portfolio facts'],
    coreBlocks: [
      { title: 'Run setup', owner: 'ResearchSettings', role: 'Select planning taxonomy, as_of_date, and lookback window for the next run' },
      { title: 'Current context', owner: 'Portfolio facts + planning context', role: 'Show the snapshot the next run will operate on' },
      { title: 'Run list', owner: 'ResearchRun', role: 'List research runs in portfolio context' },
      { title: 'Artifact viewer', owner: 'Research artifacts', role: 'Display run output without leaving the portfolio workspace' },
      { title: 'Handoff to portfolio context', owner: 'ResearchRun handoff', role: 'Promote research output into analysis or review context' },
    ],
    comparator: ['No canonical comparator; this page is workflow-oriented'],
    conditionalBlocks: ['Method templates may grow later, but this page should not become a raw backend schema editor'],
    drillDowns: [{ source: 'Research artifact', target: 'Review or portfolio discussion context' }],
    notThisPage: ['Does not replace Review narrative ownership', 'Does not expose low-level solver request JSON as the primary UI'],
  },
  taxonomies: {
    key: 'taxonomies',
    label: 'Taxonomies',
    href: '/taxonomies',
    family: 'Config',
    toolbarLabel: 'Page: Taxonomies',
    summary:
      'Taxonomies is the portfolio-scoped configuration page for classification systems, assignment coverage, default planning taxonomy selection, and planning target-set management.',
    primaryQuestion: '这个组合的分类体系和 planning targets 是什么？',
    primaryObjects: ['Taxonomy', 'TaxonomyAssignment', 'TargetSet', 'TargetSetLine'],
    sharedContext: ['portfolio_id: current workspace portfolio'],
    coreBlocks: [
      { title: 'Taxonomy list', owner: 'Taxonomy', role: 'List portfolio-scoped taxonomies and their type' },
      { title: 'Taxonomy detail and tree editor', owner: 'Taxonomy nodes', role: 'Inspect and maintain taxonomy structure' },
      { title: 'Assignment coverage and Unassigned diagnostics', owner: 'TaxonomyAssignment', role: 'Check assignment completeness and coverage issues' },
      { title: 'Default planning taxonomy selector', owner: 'Portfolio + Taxonomy', role: 'Choose which planning-enabled taxonomy drives default drift and target context' },
      { title: 'Planning target-set management', owner: 'TargetSet + TargetSetLine', role: 'Manage SAA / TAA target histories for planning-enabled taxonomies' },
    ],
    comparator: ['No benchmark comparator; this page owns classification and planning config'],
    conditionalBlocks: ['Planning-enabled taxonomies must be instrument-scoped and may optionally include cash_bucket', 'Account taxonomies can support analysis but not TargetSet ownership'],
    drillDowns: [{ source: 'Planning target set', target: 'Risk and Review target resolution' }],
    notThisPage: ['Not a high-frequency analysis workspace', 'Does not own independent benchmark analytics'],
  },
}

export function getPortfolioPageDefinition(key: string) {
  return portfolioPageDefinitions[key]
}
