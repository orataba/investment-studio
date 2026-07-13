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
  { label: 'Overview', href: '/overview' },
  { label: 'Holdings', href: '/holdings' },
  { label: 'Performance', href: '/performance' },
  { label: 'Risk', href: '/risk' },
  { label: 'Transactions', href: '/transactions' },
  { label: 'Accounts', href: '/accounts' },
  { label: 'Taxonomies', href: '/taxonomies' },
  { label: 'Research', href: '/research' },
] as const

export const portfolioPageDefinitions: Record<string, PortfolioPageDefinition> = {
  overview: {
    key: 'overview',
    label: 'Overview',
    href: '/overview',
    family: 'Current-State',
    toolbarLabel: 'Page: Portfolio Overview',
    summary:
      'Overview is the current-state portfolio report. It answers how the portfolio looks now, how it has behaved since inception, and which composition or risk facts deserve immediate follow-up.',
    primaryQuestion: '这个组合现在整体怎样？',
    primaryObjects: ['PortfolioSnapshot', 'alert summary', 'selected benchmark summary'],
    sharedContext: [
      'portfolio_id: current workspace portfolio',
      'as_of_date: latest complete as_of_date',
      'resolved_primary_benchmark_assignment_id: resolved active assignment at as_of_date',
      'base_currency: portfolio base currency',
      'nav_coverage_state + nav_coverage_reason_codes: fair-value NAV coverage only',
    ],
    coreBlocks: [
      { title: 'Portfolio summary hero', owner: 'PortfolioSnapshot', role: 'Show NAV, day move, status, and latest complete as_of_date' },
      { title: 'Current composition summary', owner: 'PortfolioSnapshot', role: 'Summarize sleeve mix, top holdings, and high-level structure' },
      { title: 'Performance summary', owner: 'PerformanceSnapshot', role: 'Show since-inception return, P&L, and drawdown context' },
      { title: 'Top holdings / top groups', owner: 'PortfolioSnapshot', role: 'Surface the names or nodes driving current composition' },
      { title: 'Risk highlights', owner: 'RiskSnapshot summary', role: 'Lift current risk issues into a compact current-state layer' },
      { title: 'Coverage highlights', owner: 'Pricing coverage summary', role: 'Call out unpriced holdings and stale shared-data gaps' },
    ],
    comparator: ['Default comparator: current sleeve mix and total NAV path', 'Benchmark-relative analysis stays secondary and links out to Performance or Risk'],
    conditionalBlocks: ['Target drift does not become a full analysis block here', 'Detailed risk analytics remains a drill-down, not the main canvas'],
    drillDowns: [
      { source: 'Composition block', target: 'Holdings grouped view' },
      { source: 'Performance summary', target: 'Performance' },
      { source: 'Risk highlights', target: 'Risk' },
      { source: 'Coverage highlights', target: 'Holdings or Database Dashboard' },
    ],
    notThisPage: ['Not a full risk cockpit', 'Not a period performance page', 'Not the canonical holdings table'],
  },
  holdings: {
    key: 'holdings',
    label: 'Holdings',
    href: '/holdings',
    family: 'Current-State',
    toolbarLabel: 'Page: Holdings',
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
      'nav_coverage_state + nav_coverage_reason_codes: fair-value NAV coverage only',
    ],
    coreBlocks: [
      { title: 'Canonical holdings table', owner: 'Positions + lots', role: 'Show market value, weight, cost basis, and unrealized state' },
      { title: 'Group by taxonomy / planning axis / account', owner: 'Taxonomy + account context', role: 'Switch between portfolio aggregation lenses' },
      { title: 'Sortable and filterable columns', owner: 'Holdings workspace', role: 'Support dense table workflows without route sprawl' },
      { title: 'Relative-to-benchmark columns', owner: 'Resolved benchmark', role: 'Expose benchmark-relative composition when supported' },
      { title: 'Security detail page entry', owner: 'Portfolio security detail route', role: 'Open quotes, transactions, trades, events, and data quality' },
    ],
    comparator: ['Default comparator: primary benchmark', 'Target gap is not the canonical comparator on this page'],
    conditionalBlocks: ['If benchmark only has return_only capability, composition-relative columns become unavailable', 'Grouping by account should drill toward Accounts rather than replace it'],
    drillDowns: [
      { source: 'Security row', target: 'Portfolio security detail page' },
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
      'nav_coverage_state + nav_coverage_reason_codes: fair-value NAV coverage',
      'book_pnl_coverage_state + book_pnl_coverage_reason_codes: accounting P&L coverage',
      'twr_reliability_status + twr_reliability_reasons: return-chain reliability',
    ],
    coreBlocks: [
      { title: 'Return scorecard', owner: 'PerformanceSnapshot', role: 'Show TWR, IRR, drawdown, and absolute result' },
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
      'Risk is the portfolio risk cockpit. It splits between Current risk and Realized risk so current structure and historical path are visible without collapsing into period performance reporting.',
    primaryQuestion: '当前风险在哪里，过去这段时间风险是怎么走出来的，现在哪些风险需要处理？',
    primaryObjects: ['RiskSnapshot', 'explicit SAA/TAA TargetSet comparators'],
    sharedContext: [
      'portfolio_id: current workspace portfolio',
      'as_of_date: latest complete as_of_date',
      'selected_taxonomy_id: planning-aware default',
      'target_comparators: SAA Weight / SAA Risk / TAA Weight / TAA Risk',
      'resolved_primary_benchmark_assignment_id: resolved active assignment at as_of_date',
      'nav_coverage_state + nav_coverage_reason_codes: fair-value input coverage only',
    ],
    coreBlocks: [
      { title: 'Rolling risk path', owner: 'RiskSnapshot + realized window', role: 'Show rolling volatility and Sharpe with optional benchmark compare' },
      { title: 'Correlation matrix', owner: 'RiskSnapshot', role: 'Show all-instrument and selected planning-taxonomy scope correlation' },
      { title: 'Current drift', owner: 'Explicit SAA/TAA TargetSet comparators', role: 'Compare actual weights and risk shares against SAA/TAA target dimensions without cross-source substitution' },
      { title: 'Risk contribution', owner: 'RiskSnapshot', role: 'Show point-in-time instrument risk contribution from the selected covariance model and contribution mode' },
    ],
    comparator: ['Target comparators: SAA Weight / SAA Risk / TAA Weight / TAA Risk', 'Benchmark-relative risk only appears when benchmark composition is available'],
    conditionalBlocks: ['Each target comparator is shown independently; a missing dimension only disables that comparator'],
    drillDowns: [
      { source: 'Drift line', target: 'Filtered Holdings' },
      { source: 'Risk budget gap line', target: 'Grouped holdings' },
    ],
    notThisPage: ['Does not produce a period narrative pack', 'Does not own the raw transaction ledger'],
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
      { source: 'Transaction instrument', target: 'Portfolio security detail page' },
      { source: 'Transaction account', target: 'Accounts' },
    ],
    notThisPage: ['Not a FIFO Trades primary page', 'Not a period or analytics summary page'],
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
      'valuation_coverage_state: account valuation coverage only',
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
  research: {
    key: 'research',
    label: 'Research',
    href: '/research',
    family: 'Period',
    toolbarLabel: 'Page: Research Runs',
    summary:
      'Research is the portfolio workbench for current target-weight solve setup, current context, run history, and artifacts.',
    primaryQuestion: '当前组合要按什么 planning 语境做研究，已经跑过哪些结果，下一步该 handoff 什么？',
    primaryObjects: ['ResearchSettings', 'ResearchRun', 'ResearchArtifact'],
    sharedContext: ['portfolio_id: current workspace portfolio', 'current default planning taxonomy', 'current portfolio facts'],
    coreBlocks: [
      { title: 'Run setup', owner: 'ResearchSettings', role: 'Select planning taxonomy, as_of_date, and lookback window for the next run' },
      { title: 'Current context', owner: 'Portfolio facts + planning context', role: 'Show the snapshot the next run will operate on' },
      { title: 'Run list', owner: 'ResearchRun', role: 'List research runs in portfolio context' },
      { title: 'Artifact viewer', owner: 'Research artifacts', role: 'Display run output without leaving the portfolio workspace' },
      { title: 'Handoff to portfolio context', owner: 'ResearchRun handoff', role: 'Promote research output into portfolio analysis context' },
    ],
    comparator: ['No canonical comparator; this page is workflow-oriented'],
    conditionalBlocks: ['Method templates may grow later, but this page should not become a raw backend schema editor'],
    drillDowns: [{ source: 'Research artifact', target: 'Portfolio discussion context' }],
    notThisPage: ['Does not replace Performance or Risk analysis ownership', 'Does not expose low-level solver request JSON as the primary UI'],
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
    drillDowns: [{ source: 'Planning target set', target: 'Risk and Research target resolution' }],
    notThisPage: ['Not a high-frequency analysis workspace', 'Does not own independent benchmark analytics'],
  },
}

export function getPortfolioPageDefinition(key: string) {
  return portfolioPageDefinitions[key]
}
