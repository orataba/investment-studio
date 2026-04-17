import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'

type PortfolioStubPageProps = {
  title: string
  summary: string
}

const sectionConfig: Record<
  string,
  {
    toolbarLabel: string
    primaryTitle: string
    primarySummary: string
    sideCards: Array<{ title: string; description: string }>
    moduleRows: Array<{ module: string; role: string }>
  }
> = {
  Accounts: {
    toolbarLabel: 'View: Account Ledger',
    primaryTitle: 'Account ledger workspace',
    primarySummary:
      'This page will split between account list selection, ledger posting history, and account-level cash or position drill-down.',
    sideCards: [
      {
        title: 'Account summary',
        description: 'Cash accounts, securities accounts, linked settlement mapping, and reconciliation status.',
      },
      {
        title: 'Posting inspector',
        description: 'Selected transaction, derived ledger postings, and source trace to portfolio activity.',
      },
    ],
    moduleRows: [
      { module: 'Account directory', role: 'List accounts by portfolio and type' },
      { module: 'Ledger table', role: 'Show account-level postings and balances' },
      { module: 'Detail rail', role: 'Open posting, transaction, or transfer details' },
    ],
  },
  Performance: {
    toolbarLabel: 'View: Total Return',
    primaryTitle: 'Performance review surface',
    primarySummary:
      'This page will carry period selectors, return cards, benchmark comparison, contribution, and drill-downs into sleeves or holdings.',
    sideCards: [
      {
        title: 'Period summary',
        description: 'Selected period, benchmark assignment, coverage state, and source freshness summary.',
      },
      {
        title: 'Attribution blocks',
        description: 'Contribution, allocation, selection, and explanation panels for period review.',
      },
    ],
    moduleRows: [
      { module: 'Return strip', role: 'Headline return metrics and benchmark deltas' },
      { module: 'Contribution table', role: 'Sort and inspect sleeve or holding contribution' },
      { module: 'Review handoff', role: 'Feed the review pack summary and export' },
    ],
  },
  Risk: {
    toolbarLabel: 'View: Current Risk',
    primaryTitle: 'Current-state risk monitor',
    primarySummary:
      'This page will become the as-of risk cockpit with drift, risk budget gap, exposure decomposition, and breach monitoring.',
    sideCards: [
      {
        title: 'Risk snapshot',
        description: 'As-of date, snapshot methodology, active target sets, and benchmark context.',
      },
      {
        title: 'Alert stack',
        description: 'Breaches, stale inputs, target mismatches, and unresolved exceptions requiring follow-up.',
      },
    ],
    moduleRows: [
      { module: 'Exposure panels', role: 'Asset class, sector, factor, and sleeve decomposition' },
      { module: 'Target compare', role: 'Weight drift and risk budget gap against resolved targets' },
      { module: 'Monitoring rail', role: 'Track alerts, source gaps, and recommended actions' },
    ],
  },
  Review: {
    toolbarLabel: 'View: Review Pack',
    primaryTitle: 'Period review editor',
    primarySummary:
      'This page will assemble the period review pack with commentary, period analytics, target timeline, actions, and export artifacts.',
    sideCards: [
      {
        title: 'Review metadata',
        description: 'Review period, pack status, export archive, and linked benchmark or target timeline state.',
      },
      {
        title: 'Action tracker',
        description: 'Carry forward open actions, new decisions, and follow-up items from the current review cycle.',
      },
    ],
    moduleRows: [
      { module: 'Narrative editor', role: 'Investment commentary and conclusions' },
      { module: 'Analytic blocks', role: 'Embed performance, risk, and target drift summaries' },
      { module: 'Export archive', role: 'Manage generated PDF or HTML review artifacts' },
    ],
  },
  'X-Ray': {
    toolbarLabel: 'View: Exposure Breakdown',
    primaryTitle: 'Cross-sectional exposure map',
    primarySummary:
      'This page will show the portfolio through allocation, factor, region, style, and issuer lenses with fast drill-down.',
    sideCards: [
      {
        title: 'Cut selector',
        description: 'Toggle between taxonomy cuts such as asset class, sector, factor, geography, or account.',
      },
      {
        title: 'Comparison slot',
        description: 'Compare current exposure against target, benchmark, or prior snapshot.',
      },
    ],
    moduleRows: [
      { module: 'Treemap or table', role: 'Primary decomposition view by selected taxonomy' },
      { module: 'Delta block', role: 'Highlight changes vs prior period or target' },
      { module: 'Drill rail', role: 'Inspect which holdings drive each exposure bucket' },
    ],
  },
  'Stock Intersection': {
    toolbarLabel: 'View: Overlap Matrix',
    primaryTitle: 'Cross-portfolio overlap workspace',
    primarySummary:
      'This page will compare overlapping underlying issuers across sleeves, watchlists, and portfolios to flag crowding or hidden concentration.',
    sideCards: [
      {
        title: 'Selection state',
        description: 'Choose portfolios, sleeves, or watchlists to include in the overlap set.',
      },
      {
        title: 'Overlap summary',
        description: 'Show overlap counts, total common exposure, and top repeated names.',
      },
    ],
    moduleRows: [
      { module: 'Overlap table', role: 'List common issuers and aggregate exposure' },
      { module: 'Matrix view', role: 'Compare pairwise overlap across selected sets' },
      { module: 'Exception list', role: 'Surface outsized overlaps or conflicts with target policy' },
    ],
  },
}

export default function PortfolioStubPage({ title, summary }: PortfolioStubPageProps) {
  const config = sectionConfig[title] ?? {
    toolbarLabel: 'View: Section Skeleton',
    primaryTitle: `${title} workspace`,
    primarySummary: summary,
    sideCards: [
      {
        title: 'Section summary',
        description: 'This panel will hold the section summary, coverage state, and selection context.',
      },
      {
        title: 'Supporting rail',
        description: 'This panel will carry drill-downs, notes, and workflow actions.',
      },
    ],
    moduleRows: [
      { module: 'Primary body', role: 'Main working surface for this section' },
      { module: 'Detail rail', role: 'Secondary detail and explanation blocks' },
      { module: 'Actions', role: 'Exports, notes, and operational controls' },
    ],
  }

  return (
    <PortfolioWorkspaceLayout activeSection={title} toolbarLabel={config.toolbarLabel}>
      <section className="panel">
        <div className="panel-header">
          <div>
            <div className="panel-title">{title}</div>
          </div>
          <div className="toolbar">
            <span>Page skeleton</span>
          </div>
        </div>
        <div className="stub-body">
          <p>{summary}</p>
        </div>
      </section>

      <section className="stub-layout-grid">
        <article className="panel stub-main-panel">
          <div className="panel-header">
            <div className="panel-title">{config.primaryTitle}</div>
            <div className="toolbar">
              <span>Primary workspace</span>
            </div>
          </div>
          <div className="stub-card-body">
            <p>{config.primarySummary}</p>
          </div>
          <div className="placeholder-surface">
            <div className="placeholder-surface-toolbar">
              <span className="placeholder-chip">Filters</span>
              <span className="placeholder-chip">Columns</span>
              <span className="placeholder-chip">Grouping</span>
            </div>
            <div className="placeholder-canvas">
              <div className="placeholder-canvas-header">
                <span>Primary body placeholder</span>
                <span>Table / chart / editor</span>
              </div>
              <div className="placeholder-canvas-grid">
                <div />
                <div />
                <div />
                <div />
              </div>
            </div>
          </div>
        </article>

        <div className="stub-side-stack">
          {config.sideCards.map((block) => (
            <article className="panel stub-card" key={block.title}>
              <div className="panel-header">
                <div className="panel-title">{block.title}</div>
              </div>
              <div className="stub-card-body">
                <p>{block.description}</p>
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <div className="panel-title">Expected modules</div>
        </div>
        <div className="placeholder-table-shell">
          <div className="placeholder-row placeholder-row-header">
            <span>Module</span>
            <span>Status</span>
            <span>Planned role</span>
          </div>
          {config.moduleRows.map((row) => (
            <div className="placeholder-row" key={row.module}>
              <span>{row.module}</span>
              <span>Stub</span>
              <span>{row.role}</span>
            </div>
          ))}
        </div>
      </section>
    </PortfolioWorkspaceLayout>
  )
}
