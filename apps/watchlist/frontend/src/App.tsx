import { Navigate, Route, Routes } from 'react-router'
import { LanguageSelector } from '../../../../packages/ui/src/i18n'

import MonitoringPage from './pages/MonitoringPage'
import WatchlistEntryPage from './pages/WatchlistEntryPage'
import WatchlistsPage from './pages/WatchlistsPage'
import InstrumentDetailPage from './pages/InstrumentDetailPage'
import SectionStubPage from './pages/SectionStubPage'

export default function App() {
  return (
    <div className="app-shell">
      <div className="app-utility-bar">
        <LanguageSelector />
      </div>
      <main className="page-shell page-shell-terminal">
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
      </main>
    </div>
  )
}
