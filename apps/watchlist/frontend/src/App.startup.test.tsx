// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useNavigate } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'
import App from './App'

const loaded = vi.hoisted(() => vi.fn())
vi.mock('./pages/WatchlistsPage', () => {
  loaded('watchlist')
  return { default: () => <p>Watchlist data</p> }
})
vi.mock('./pages/InstrumentDetailPage', () => {
  loaded('instrument')
  return { default: () => <p>Instrument data</p> }
})
vi.mock('./pages/WatchlistEntryPage', () => {
  loaded('entry')
  return { default: () => <p>Watchlist entry</p> }
})
vi.mock('./pages/MonitoringPage', () => {
  loaded('monitoring')
  return { default: () => <p>Monitoring data</p> }
})
vi.mock('./pages/ResearchPage', () => {
  loaded('research')
  return { default: () => <p>Research data</p> }
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

function Navigation() {
  const navigate = useNavigate()
  return <button onClick={() => navigate('/instruments/fund-a')}>Open instrument</button>
}

it('loads only the requested route alongside identity and renders only the latest authorized route', async () => {
  let finishIdentity!: (response: unknown) => void
  const fetch = vi.fn(() => new Promise(resolve => { finishIdentity = resolve }))
  vi.stubGlobal('fetch', fetch)
  render(<MemoryRouter initialEntries={['/watchlists/alpha']}><Navigation /><App /></MemoryRouter>)
  await waitFor(() => expect(loaded).toHaveBeenCalledWith('watchlist'))
  expect(fetch).toHaveBeenCalledOnce()
  expect(screen.queryByText('Watchlist data')).toBeNull()
  expect(loaded.mock.calls.map(([page]) => page)).toEqual(['watchlist'])

  fireEvent.click(screen.getByRole('button', { name: 'Open instrument' }))
  await waitFor(() => expect(loaded).toHaveBeenCalledWith('instrument'))
  expect(screen.queryByText('Instrument data')).toBeNull()
  await act(async () => finishIdentity({ ok: true, json: async () => ({
    user_id: 'reader', display_name: 'Reader', team_id: 'team', team_role: 'reader',
  }) }))
  expect(await screen.findByText('Instrument data')).toBeTruthy()
  expect(screen.queryByText('Watchlist data')).toBeNull()
  expect(loaded.mock.calls.map(([page]) => page)).toEqual(['watchlist', 'instrument'])
  expect(fetch).toHaveBeenCalledOnce()
})
