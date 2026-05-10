import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'

type PortfolioStubPageProps = {
  title: string
  summary: string
}

export default function PortfolioStubPage({ title }: PortfolioStubPageProps) {
  return (
    <PortfolioWorkspaceLayout activeSection={title} toolbarLabel="View">
      <section className="panel">
        <div className="panel-header">
          <div className="panel-title">{title}</div>
        </div>
        <div className="empty-state">No data.</div>
      </section>
    </PortfolioWorkspaceLayout>
  )
}
