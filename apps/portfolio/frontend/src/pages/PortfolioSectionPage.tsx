import PortfolioPageBlueprint from '../components/PortfolioPageBlueprint'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import { getPortfolioPageDefinition } from '../lib/portfolioIa'

type PortfolioSectionPageProps = {
  pageKey: string
}

export default function PortfolioSectionPage({ pageKey }: PortfolioSectionPageProps) {
  const definition = getPortfolioPageDefinition(pageKey)

  if (!definition) {
    return null
  }

  return (
    <PortfolioWorkspaceLayout activeSection={definition.label} toolbarLabel={definition.toolbarLabel}>
      <PortfolioPageBlueprint definition={definition} />
    </PortfolioWorkspaceLayout>
  )
}
