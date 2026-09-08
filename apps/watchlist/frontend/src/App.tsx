import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router'

import './research-workbench.css'

import LoadingOverlay from './components/LoadingOverlay'
import AccountBoundary from './components/AccountBoundary'

const InstrumentDetailPage = lazy(() => import('./pages/InstrumentDetailPage'))
const MonitoringPage = lazy(() => import('./pages/MonitoringPage'))
const WatchlistEntryPage = lazy(() => import('./pages/WatchlistEntryPage'))
const WatchlistsPage = lazy(() => import('./pages/WatchlistsPage'))

const ResearchPage = lazy(() => import('./pages/ResearchPage'))

export default function App() {
  return (
    <AccountBoundary><div className="app-shell">
      <main className="page-shell page-shell-terminal">
        <Suspense fallback={<LoadingOverlay label="Loading page" />}>
          <Routes>
            <Route path="/assistant" element={<ResearchPage />} />
            <Route path="/" element={<WatchlistEntryPage />} />
            <Route path="/watchlists" element={<WatchlistEntryPage />} />
            <Route path="/watchlists/:watchlistId" element={<WatchlistsPage />} />
            <Route path="/instruments/:instrumentId" element={<InstrumentDetailPage />} />
            <Route
              path="/monitoring"
              element={<MonitoringPage />}
            />
            <Route path="*" element={<Navigate replace to="/watchlists" />} />
          </Routes>
        </Suspense>
      </main>
    </div></AccountBoundary>
  )
}
