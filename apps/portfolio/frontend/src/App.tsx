import { Suspense, lazy, useEffect } from 'react'
import { Navigate, Route, Routes, matchPath, useLocation } from 'react-router'
import PortfolioBootstrapProvider from './components/PortfolioBootstrapProvider'
import CalculationStatus from './components/CalculationStatus'
import PortfolioSessionProvider from './components/PortfolioSessionProvider'
import PortfolioAccessProvider from './components/PortfolioAccessProvider'
import PortfolioCapabilitiesProvider, { usePortfolioCapabilities } from './components/PortfolioCapabilitiesProvider'

const pages = {
  accounts: () => import('./pages/AccountsPage'),
  overview: () => import('./pages/OverviewPage'),
  holdings: () => import('./pages/PortfolioHomePage'),
  holdingDetail: () => import('./pages/PortfolioHoldingDetailPage'),
  portfolios: () => import('./pages/PortfoliosPage'),
  performance: () => import('./pages/PerformancePage'),
  research: () => import('./pages/ResearchPage'),
  risk: () => import('./pages/RiskPage'),
  taxonomies: () => import('./pages/TaxonomiesPage'),
  transactions: () => import('./pages/TransactionsPage'),
}
const AccountsPage = lazy(pages.accounts)
const OverviewPage = lazy(pages.overview)
const PortfolioHomePage = lazy(pages.holdings)
const PortfolioHoldingDetailPage = lazy(pages.holdingDetail)
const PortfoliosPage = lazy(pages.portfolios)
const PerformancePage = lazy(pages.performance)
const ResearchPage = lazy(pages.research)
const RiskPage = lazy(pages.risk)
const TaxonomiesPage = lazy(pages.taxonomies)
const TransactionsPage = lazy(pages.transactions)

function pageForPath(path: string): { portfolioId: string | null; load: () => Promise<unknown> } {
  const match = matchPath('/portfolios/:portfolioId/*', path)
  if (!match) return { portfolioId: null, load: pages.portfolios }
  const section = match.params['*'] || 'overview'
  if (matchPath('/portfolios/:portfolioId/holdings/:holdingId', path)) return { portfolioId: match.params.portfolioId!, load: pages.holdingDetail }
  const name = section === 'snapshot' ? 'overview' : section
  if (!Object.prototype.hasOwnProperty.call(pages, name) || name === 'portfolios' || name === 'holdingDetail') return { portfolioId: null, load: pages.portfolios }
  return { portfolioId: match.params.portfolioId!, load: pages[name as keyof typeof pages] }
}

function PageFallback() {
  return <CalculationStatus />
}

function ResearchRoute() {
  const { research_enabled } = usePortfolioCapabilities()
  return research_enabled
    ? <ResearchPage />
    : <Navigate replace relative="path" to="../overview" />
}

export default function App() {
  const { pathname } = useLocation()
  const { portfolioId, load } = pageForPath(pathname)
  useEffect(() => {
    // Download only this route while account/ACL bootstrap is in flight.
    // Rendering still waits for authority; lazy() surfaces a module load error.
    void load().catch(() => undefined)
  }, [load])
  return (
    <div className="app-shell">
      <main className="page-shell page-shell-terminal">
        <PortfolioBootstrapProvider portfolioId={portfolioId}><PortfolioSessionProvider><PortfolioCapabilitiesProvider>
          <Suspense fallback={<PageFallback />}>
            <Routes>
              <Route path="/" element={<Navigate replace to="/portfolios" />} />
              <Route path="/portfolios" element={<PortfoliosPage />} />
              <Route path="/portfolios/:portfolioId" element={<Navigate replace to="overview" />} />
              <Route path="/portfolios/:portfolioId/snapshot" element={<Navigate replace to="../overview" />} />
              <Route path="/portfolios/:portfolioId/overview" element={<PortfolioAccessProvider><OverviewPage /></PortfolioAccessProvider>} />
              <Route path="/portfolios/:portfolioId/holdings" element={<PortfolioAccessProvider><PortfolioHomePage /></PortfolioAccessProvider>} />
              <Route path="/portfolios/:portfolioId/holdings/:holdingId" element={<PortfolioAccessProvider><PortfolioHoldingDetailPage /></PortfolioAccessProvider>} />
              <Route path="/portfolios/:portfolioId/performance" element={<PortfolioAccessProvider><PerformancePage /></PortfolioAccessProvider>} />
              <Route path="/portfolios/:portfolioId/risk" element={<PortfolioAccessProvider><RiskPage /></PortfolioAccessProvider>} />
              <Route path="/portfolios/:portfolioId/transactions" element={<PortfolioAccessProvider><TransactionsPage /></PortfolioAccessProvider>} />
              <Route path="/portfolios/:portfolioId/accounts" element={<PortfolioAccessProvider><AccountsPage /></PortfolioAccessProvider>} />
              <Route path="/portfolios/:portfolioId/taxonomies" element={<PortfolioAccessProvider><TaxonomiesPage /></PortfolioAccessProvider>} />
              <Route path="/portfolios/:portfolioId/research" element={<PortfolioAccessProvider><ResearchRoute /></PortfolioAccessProvider>} />
              <Route path="/snapshot" element={<Navigate replace to="/portfolios" />} />
              <Route path="/overview" element={<Navigate replace to="/portfolios" />} />
              <Route path="/holdings" element={<Navigate replace to="/portfolios" />} />
              <Route path="/performance" element={<Navigate replace to="/portfolios" />} />
              <Route path="/risk" element={<Navigate replace to="/portfolios" />} />
              <Route path="/transactions" element={<Navigate replace to="/portfolios" />} />
              <Route path="/accounts" element={<Navigate replace to="/portfolios" />} />
              <Route path="/research" element={<Navigate replace to="/portfolios" />} />
              <Route path="/taxonomies" element={<Navigate replace to="/portfolios" />} />
              <Route path="/x-ray" element={<Navigate replace to="/portfolios" />} />
              <Route path="/stock-intersection" element={<Navigate replace to="/portfolios" />} />
              <Route path="*" element={<Navigate replace to="/portfolios" />} />
            </Routes>
          </Suspense>
        </PortfolioCapabilitiesProvider></PortfolioSessionProvider></PortfolioBootstrapProvider>
      </main>
    </div>
  )
}
