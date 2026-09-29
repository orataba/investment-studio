import { act, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import PortfolioCalculationNotice from './components/PortfolioCalculationNotice'

const api = vi.hoisted(() => ({ getPortfolioCalculationStatus: vi.fn() }))
vi.mock('./lib/api', () => api)
afterEach(() => { vi.useRealTimers(); vi.resetAllMocks() })

it('keeps the update toast until the durable calculation completes', async () => {
  vi.useFakeTimers()
  api.getPortfolioCalculationStatus.mockResolvedValueOnce({ status: 'running' }).mockResolvedValueOnce({ status: 'current', refreshed_to: '2026-09-29' })
  render(<PortfolioCalculationNotice portfolioId="3" revision={1} />)
  await act(async () => {})
  expect(screen.getByRole('status')).toHaveTextContent('更新中')
  await act(async () => { vi.advanceTimersByTime(2000) })
  expect(screen.getByRole('status')).toHaveTextContent('已更新至 2026-09-29')
  expect(api.getPortfolioCalculationStatus).toHaveBeenCalledTimes(2)
})

it('shows the calculation failure rather than an indefinite updating state', async () => {
  api.getPortfolioCalculationStatus.mockResolvedValue({ status: 'failed', error_message: 'Missing FX' })
  render(<PortfolioCalculationNotice portfolioId="3" revision={1} />)
  expect(await screen.findByRole('alert')).toHaveTextContent('净值更新未完成：Missing FX')
})

it('does not carry a previous portfolio update notice into a failed status read', async () => {
  api.getPortfolioCalculationStatus.mockResolvedValueOnce({ status: 'running' }).mockRejectedValueOnce(new Error('Network unavailable'))
  const view = render(<PortfolioCalculationNotice portfolioId="3" revision={1} />)
  expect(await screen.findByRole('status')).toHaveTextContent('更新中')
  view.rerender(<PortfolioCalculationNotice portfolioId="4" revision={1} />)
  await act(async () => {})
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
})
