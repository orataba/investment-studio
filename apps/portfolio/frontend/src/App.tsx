import { Suspense, lazy } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { LanguageSelector } from '../../../../packages/ui/src/i18n'

const AccountsPage = lazy(() => import('./pages/AccountsPage'))
const OverviewPage = lazy(() => import('./pages/OverviewPage'))
const PortfolioHomePage = lazy(() => import('./pages/PortfolioHomePage'))
const PortfolioSecurityDetailPage = lazy(() => import('./pages/PortfolioSecurityDetailPage'))
const PortfoliosPage = lazy(() => import('./pages/PortfoliosPage'))
const PerformancePage = lazy(() => import('./pages/PerformancePage'))
const ResearchPage = lazy(() => import('./pages/ResearchPage'))
const ReviewPage = lazy(() => import('./pages/ReviewPage'))
const RiskPage = lazy(() => import('./pages/RiskPage'))
const TaxonomiesPage = lazy(() => import('./pages/TaxonomiesPage'))
const TransactionsPage = lazy(() => import('./pages/TransactionsPage'))

function PageFallback() {
  return <div className="empty-state">Loading page...</div>
}

export default function App() {
  return (
    <div className="app-shell">
      <div className="app-utility-bar">
        <LanguageSelector />
      </div>
      <main className="page-shell page-shell-terminal">
        <Suspense fallback={<PageFallback />}>
          <Routes>
            <Route path="/" element={<Navigate replace to="/portfolios" />} />
            <Route path="/portfolios" element={<PortfoliosPage />} />
            <Route path="/portfolios/:portfolioId" element={<Navigate replace to="overview" />} />
            <Route path="/portfolios/:portfolioId/snapshot" element={<Navigate replace to="../overview" />} />
            <Route path="/portfolios/:portfolioId/overview" element={<OverviewPage />} />
            <Route path="/portfolios/:portfolioId/holdings" element={<PortfolioHomePage />} />
            <Route path="/portfolios/:portfolioId/holdings/:instrumentId" element={<PortfolioSecurityDetailPage />} />
            <Route path="/portfolios/:portfolioId/performance" element={<PerformancePage />} />
            <Route path="/portfolios/:portfolioId/risk" element={<RiskPage />} />
            <Route path="/portfolios/:portfolioId/transactions" element={<TransactionsPage />} />
            <Route path="/portfolios/:portfolioId/accounts" element={<AccountsPage />} />
            <Route path="/portfolios/:portfolioId/review" element={<ReviewPage />} />
            <Route path="/portfolios/:portfolioId/research" element={<ResearchPage />} />
            <Route path="/portfolios/:portfolioId/taxonomies" element={<TaxonomiesPage />} />
            <Route path="/snapshot" element={<Navigate replace to="/portfolios" />} />
            <Route path="/overview" element={<Navigate replace to="/portfolios" />} />
            <Route path="/holdings" element={<Navigate replace to="/portfolios" />} />
            <Route path="/performance" element={<Navigate replace to="/portfolios" />} />
            <Route path="/risk" element={<Navigate replace to="/portfolios" />} />
            <Route path="/transactions" element={<Navigate replace to="/portfolios" />} />
            <Route path="/accounts" element={<Navigate replace to="/portfolios" />} />
            <Route path="/review" element={<Navigate replace to="/portfolios" />} />
            <Route path="/research" element={<Navigate replace to="/portfolios" />} />
            <Route path="/taxonomies" element={<Navigate replace to="/portfolios" />} />
            <Route path="/x-ray" element={<Navigate replace to="/portfolios" />} />
            <Route path="/stock-intersection" element={<Navigate replace to="/portfolios" />} />
          </Routes>
        </Suspense>
      </main>
    </div>
  )
}
