// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'
import InstrumentDetailPage from './InstrumentDetailPage'

const api = vi.hoisted(() => ({ resolve: vi.fn(), watchlist: vi.fn() }))
vi.mock('../lib/api', () => ({ resolveInstrumentDetail: api.resolve, getWatchlistDetail: api.watchlist }))
vi.mock('./ListedInstrumentDetailPage', () => ({ default: ({ instrument }: { instrument: { instrument_name: string } }) => <div>{instrument.instrument_name} workspace</div> }))
vi.mock('./PublicFundDetailPage', () => ({ default: () => null }))
vi.mock('./PrivateFundDetailPage', () => ({ default: () => null }))
afterEach(() => { cleanup(); vi.clearAllMocks() })

it('renders a resolved instrument while the optional breadcrumb remains pending', async () => {
  api.resolve.mockResolvedValue({ instrument_name: 'Technology', instrument_type: 'etf', detail_supported: true, detail_subject_id: 'xlk' })
  api.watchlist.mockReturnValue(new Promise(() => {}))
  render(<MemoryRouter initialEntries={['/instruments/xlk?watchlist=slow-list&tab=investment-research']}>
    <Routes><Route path="/instruments/:instrumentId" element={<InstrumentDetailPage />} /></Routes>
  </MemoryRouter>)
  expect(await screen.findByText('Technology workspace')).toBeTruthy()
  expect(screen.queryByText('Loading')).toBeNull()
  expect(api.resolve).toHaveBeenCalledWith('xlk')
})
