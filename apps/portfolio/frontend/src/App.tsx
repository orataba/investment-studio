import { Suspense, lazy } from 'react'
import { Navigate, Route, Routes } from 'react-router'
import CalculationStatus from './components/CalculationStatus'
import PortfolioSessionProvider from './components/PortfolioSessionProvider'
import PortfolioAccessProvider from './components/PortfolioAccessProvider'
import PortfolioCapabilitiesProvider, { usePortfolioCapabilities } from './components/PortfolioCapabilitiesProvider'

const AccountsPage = lazy(() => import('./pages/AccountsPage'))
const OverviewPage = lazy(() => import('./pages/OverviewPage'))
const PortfolioHomePage = lazy(() => import('./pages/PortfolioHomePage'))
const PortfolioHoldingDetailPage = lazy(() => import('./pages/PortfolioHoldingDetailPage'))
const PortfoliosPage = lazy(() => import('./pages/PortfoliosPage'))
const PerformancePage = lazy(() => import('./pages/PerformancePage'))
const ResearchPage = lazy(() => import('./pages/ResearchPage'))
const RiskPage = lazy(() => import('./pages/RiskPage'))
const TaxonomiesPage = lazy(() => import('./pages/TaxonomiesPage'))
const TransactionsPage = lazy(() => import('./pages/TransactionsPage'))

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
  return (
    <div className="app-shell">
      <main className="page-shell page-shell-terminal">
        <PortfolioSessionProvider><PortfolioCapabilitiesProvider>
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
        </PortfolioCapabilitiesProvider></PortfolioSessionProvider>
      </main>
    </div>
  )
}
