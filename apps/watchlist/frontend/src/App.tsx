import { lazy, Suspense, useEffect } from 'react'
import { matchPath, Navigate, Route, Routes, useLocation } from 'react-router'

import './research-workbench.css'

import LoadingOverlay from './components/LoadingOverlay'
import AccountBoundary from './components/AccountBoundary'

const pages = {
  instrument: () => import('./pages/InstrumentDetailPage'),
  monitoring: () => import('./pages/MonitoringPage'),
  entry: () => import('./pages/WatchlistEntryPage'),
  watchlist: () => import('./pages/WatchlistsPage'),
  research: () => import('./pages/ResearchPage'),
}
const InstrumentDetailPage = lazy(pages.instrument)
const MonitoringPage = lazy(pages.monitoring)
const WatchlistEntryPage = lazy(pages.entry)
const WatchlistsPage = lazy(pages.watchlist)
const ResearchPage = lazy(pages.research)

function pageForPath(pathname: string) {
  if (matchPath('/instruments/:instrumentId', pathname)) return pages.instrument
  if (matchPath('/watchlists/:watchlistId', pathname)) return pages.watchlist
  if (matchPath('/monitoring', pathname)) return pages.monitoring
  if (matchPath('/assistant', pathname)) return pages.research
  return pages.entry
}

export default function App() {
  const { pathname } = useLocation()
  const load = pageForPath(pathname)
  useEffect(() => {
    // Fetch only the current page's code alongside identity. AccountBoundary
    // still prevents rendering the page or reading its data without authority.
    void load().catch(() => undefined)
  }, [load])
  return (
    <AccountBoundary><div className="app-shell">
      <main className="page-shell page-shell-terminal">
        <Suspense fallback={<LoadingOverlay />}>
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
