import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router'
import { LanguageSelector } from '../../../../packages/ui/src/i18n'

import LoadingOverlay from './components/LoadingOverlay'

const InstrumentDetailPage = lazy(() => import('./pages/InstrumentDetailPage'))
const MonitoringPage = lazy(() => import('./pages/MonitoringPage'))
const SectionStubPage = lazy(() => import('./pages/SectionStubPage'))
const WatchlistEntryPage = lazy(() => import('./pages/WatchlistEntryPage'))
const WatchlistsPage = lazy(() => import('./pages/WatchlistsPage'))

export default function App() {
  return (
    <div className="app-shell">
      <div className="app-utility-bar">
        <LanguageSelector />
      </div>
      <main className="page-shell page-shell-terminal">
        <Suspense fallback={<LoadingOverlay label="Loading page" />}>
          <Routes>
            <Route path="/" element={<WatchlistEntryPage />} />
            <Route path="/watchlists" element={<WatchlistEntryPage />} />
            <Route path="/watchlists/:watchlistId" element={<WatchlistsPage />} />
            <Route path="/instruments/:instrumentId" element={<InstrumentDetailPage />} />
            <Route
              path="/research"
              element={
                <SectionStubPage
                  title="Research"
                  summary="Research is intentionally a second-layer capability in v2 rather than the first-screen shell."
                />
              }
            />
            <Route
              path="/documents"
              element={
                <SectionStubPage
                  title="Documents & Imports"
                  summary="This route will reconnect email sync, OCR extraction, valuation statement ingestion, and document adoption workflows."
                />
              }
            />
            <Route
              path="/monitoring"
              element={<MonitoringPage />}
            />
            <Route path="*" element={<Navigate replace to="/watchlists" />} />
          </Routes>
        </Suspense>
      </main>
    </div>
  )
}
