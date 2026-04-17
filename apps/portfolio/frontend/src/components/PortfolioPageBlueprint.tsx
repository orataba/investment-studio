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
    <>
      {!compact ? (
        <section className="panel">
          <div className="panel-header">
            <div className="panel-title">{definition.label}</div>
            <div className="toolbar">
              <span>{definition.family}</span>
              <span>Doc-aligned scaffold</span>
            </div>
          </div>
          <div className="stub-body">
            <p>{definition.summary}</p>
          </div>
        </section>
      ) : null}

      <section className="stub-layout-grid">
        <article className="panel stub-main-panel">
          <div className="panel-header">
            <div className="panel-title">Primary Question</div>
            <div className="toolbar">
              <span>{definition.family}</span>
            </div>
          </div>
          <div className="stub-card-body">
            <p>{definition.primaryQuestion}</p>
          </div>
          <div className="placeholder-table-shell">
            <div className="placeholder-row placeholder-row-header blueprint-row-header">
              <span>Core block</span>
              <span>Owner</span>
              <span>Planned role</span>
            </div>
            {definition.coreBlocks.map((block) => (
              <div className="placeholder-row blueprint-row" key={block.title}>
                <span>{block.title}</span>
                <span>{block.owner}</span>
                <span>{block.role}</span>
              </div>
            ))}
          </div>
        </article>

        <div className="stub-side-stack">
          <article className="panel stub-card">
            <div className="panel-header">
              <div className="panel-title">Primary Objects</div>
            </div>
            <div className="stub-card-body">
              <div className="doc-list">
                {definition.primaryObjects.map((item) => (
                  <span className="doc-list-row" key={item}>
                    {item}
                  </span>
                ))}
              </div>
            </div>
          </article>

          <article className="panel stub-card">
            <div className="panel-header">
              <div className="panel-title">Shared Context</div>
            </div>
            <div className="stub-card-body">
              <div className="doc-list">
                {definition.sharedContext.map((item) => (
                  <span className="doc-list-row" key={item}>
                    {item}
                  </span>
                ))}
              </div>
            </div>
          </article>
        </div>
      </section>

      <section className="three-panel-grid">
        <article className="panel stub-card">
          <div className="panel-header">
            <div className="panel-title">Comparator & Rules</div>
          </div>
          <div className="stub-card-body">
            <div className="doc-list">
              {definition.comparator.map((item) => (
                <span className="doc-list-row" key={item}>
                  {item}
                </span>
              ))}
              {definition.conditionalBlocks.map((item) => (
                <span className="doc-list-row doc-list-row-muted" key={item}>
                  {item}
                </span>
              ))}
            </div>
          </div>
        </article>

        <article className="panel stub-card">
          <div className="panel-header">
            <div className="panel-title">Drill-Down</div>
          </div>
          <div className="stub-card-body">
            <div className="doc-list">
              {definition.drillDowns.map((item) => (
                <span className="doc-list-row" key={`${item.source}-${item.target}`}>
                  {item.source} -&gt; {item.target}
                </span>
              ))}
            </div>
          </div>
        </article>

        <article className="panel stub-card">
          <div className="panel-header">
            <div className="panel-title">Not This Page</div>
          </div>
          <div className="stub-card-body">
            <div className="doc-list">
              {definition.notThisPage.map((item) => (
                <span className="doc-list-row doc-list-row-muted" key={item}>
                  {item}
                </span>
              ))}
            </div>
          </div>
        </article>
      </section>
    </>
  )
}
