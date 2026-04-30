import { type PortfolioPageDefinition } from '../lib/portfolioIa'

type PortfolioPageBlueprintProps = {
  definition: PortfolioPageDefinition
  compact?: boolean
}

export default function PortfolioPageBlueprint({
  definition,
  compact = false,
}: PortfolioPageBlueprintProps) {
  return (
    <section className="panel">
      {!compact ? (
        <div className="panel-header">
          <div className="panel-title">{definition.label}</div>
        </div>
      ) : null}
      <div className="empty-state">No data available.</div>
    </section>
  )
}
