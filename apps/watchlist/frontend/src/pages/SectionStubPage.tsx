import { Link } from 'react-router-dom'

import { PLATFORM_HOME_URL } from '../lib/navigation'

type SectionStubPageProps = {
  title: string
  summary: string
}

export default function SectionStubPage({
  title,
  summary,
}: SectionStubPageProps) {
  return (
    <section className="panel">
      <div className="stub-breadcrumbs">
        <a href={PLATFORM_HOME_URL} className="watchlist-breadcrumb-link">
          Home
        </a>
        <span className="watchlist-breadcrumb-separator">/</span>
        <Link to="/watchlists" className="watchlist-breadcrumb-link">
          Watchlist
        </Link>
        <span className="watchlist-breadcrumb-separator">/</span>
        <span className="watchlist-breadcrumb-current">{title}</span>
      </div>
      <div className="panel-header">
        <div>
          <div className="panel-title">{title}</div>
          <h1 className="page-title">{title}</h1>
        </div>
      </div>
      <div className="stub-body">
        <p>{summary}</p>
        <p className="muted" style={{ marginBottom: 0 }}>
          This section is intentionally lightweight for now. The v2 priority
          remains watchlists, fund/index detail, canonical facts, recalculation jobs,
          and read models.
        </p>
      </div>
    </section>
  )
}
