import { Navigate, Route, Routes } from 'react-router-dom'

import { DEFAULT_PORTFOLIO_ID } from './lib/navigation'
import AccountsPage from './pages/AccountsPage'
import PortfolioHomePage from './pages/PortfolioHomePage'
import PortfoliosPage from './pages/PortfoliosPage'
import PerformancePage from './pages/PerformancePage'
import PortfolioSectionPage from './pages/PortfolioSectionPage'
import ResearchPage from './pages/ResearchPage'
import RiskPage from './pages/RiskPage'
import TaxonomiesPage from './pages/TaxonomiesPage'
import TransactionsPage from './pages/TransactionsPage'

export default function App() {
  return (
    <div className="app-shell">
      <main className="page-shell page-shell-terminal">
        <Routes>
          <Route path="/" element={<Navigate replace to="/portfolios" />} />
          <Route path="/portfolios" element={<PortfoliosPage />} />
          <Route path="/portfolios/:portfolioId" element={<Navigate replace to="holdings" />} />
          <Route path="/portfolios/:portfolioId/snapshot" element={<PortfolioSectionPage pageKey="snapshot" />} />
          <Route path="/portfolios/:portfolioId/holdings" element={<PortfolioHomePage />} />
          <Route path="/portfolios/:portfolioId/performance" element={<PerformancePage />} />
          <Route path="/portfolios/:portfolioId/risk" element={<RiskPage />} />
          <Route path="/portfolios/:portfolioId/transactions" element={<TransactionsPage />} />
          <Route path="/portfolios/:portfolioId/accounts" element={<AccountsPage />} />
          <Route path="/portfolios/:portfolioId/review" element={<PortfolioSectionPage pageKey="review" />} />
          <Route path="/portfolios/:portfolioId/research" element={<ResearchPage />} />
          <Route path="/portfolios/:portfolioId/taxonomies" element={<TaxonomiesPage />} />
          <Route path="/snapshot" element={<Navigate replace to={`/portfolios/${DEFAULT_PORTFOLIO_ID}/snapshot`} />} />
          <Route path="/holdings" element={<Navigate replace to={`/portfolios/${DEFAULT_PORTFOLIO_ID}/holdings`} />} />
          <Route path="/performance" element={<Navigate replace to={`/portfolios/${DEFAULT_PORTFOLIO_ID}/performance`} />} />
          <Route path="/risk" element={<Navigate replace to={`/portfolios/${DEFAULT_PORTFOLIO_ID}/risk`} />} />
          <Route path="/transactions" element={<Navigate replace to={`/portfolios/${DEFAULT_PORTFOLIO_ID}/transactions`} />} />
          <Route path="/accounts" element={<Navigate replace to={`/portfolios/${DEFAULT_PORTFOLIO_ID}/accounts`} />} />
          <Route path="/review" element={<Navigate replace to={`/portfolios/${DEFAULT_PORTFOLIO_ID}/review`} />} />
          <Route path="/research" element={<Navigate replace to={`/portfolios/${DEFAULT_PORTFOLIO_ID}/research`} />} />
          <Route path="/taxonomies" element={<Navigate replace to={`/portfolios/${DEFAULT_PORTFOLIO_ID}/taxonomies`} />} />
          <Route path="/x-ray" element={<Navigate replace to={`/portfolios/${DEFAULT_PORTFOLIO_ID}/snapshot`} />} />
          <Route path="/stock-intersection" element={<Navigate replace to={`/portfolios/${DEFAULT_PORTFOLIO_ID}/holdings`} />} />
        </Routes>
      </main>
    </div>
  )
}
