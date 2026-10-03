import { act, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { LanguageProvider } from '../../../../packages/ui/src/i18n'
import PortfolioCalculationNotice from './components/PortfolioCalculationNotice'

const api = vi.hoisted(() => ({ getPortfolioCalculationStatus: vi.fn() }))
vi.mock('./lib/api', () => api)
afterEach(() => { vi.useRealTimers(); vi.resetAllMocks() })

it('keeps update progress in the portfolio until the durable calculation completes', async () => {
  vi.useFakeTimers()
  api.getPortfolioCalculationStatus.mockResolvedValueOnce({ status: 'running' }).mockResolvedValueOnce({ status: 'current', refreshed_to: '2026-09-29' })
  const { container } = render(<LanguageProvider enableDomTranslation={false}><PortfolioCalculationNotice portfolioId="3" revision={1} /></LanguageProvider>)
  await act(async () => {})
  expect(screen.getByRole('status')).toHaveTextContent('Updating portfolio valuation and holdings')
  expect(container).toContainElement(screen.getByRole('status'))
  expect(document.querySelector('.investment-studio-notice-toast')).toBeNull()
  await act(async () => { vi.advanceTimersByTime(2000) })
  expect(screen.getByRole('status')).toHaveTextContent('updated through 2026-09-29')
  expect(container.querySelector('.investment-studio-loading')).toBeNull()
  expect(screen.getByRole('status')).toHaveClass('investment-studio-notice-toast-success')
  expect(api.getPortfolioCalculationStatus).toHaveBeenCalledTimes(2)
})

it('shows the calculation failure rather than an indefinite updating state', async () => {
  api.getPortfolioCalculationStatus.mockResolvedValue({ status: 'failed', error_message: 'Missing FX' })
  render(<LanguageProvider enableDomTranslation={false}><PortfolioCalculationNotice portfolioId="3" revision={1} /></LanguageProvider>)
  expect(await screen.findByRole('alert')).toHaveTextContent('Valuation update incomplete: Missing FX')
})

it('does not carry a previous portfolio update notice into a failed status read', async () => {
  api.getPortfolioCalculationStatus.mockResolvedValueOnce({ status: 'running' }).mockRejectedValueOnce(new Error('Network unavailable'))
  const view = render(<LanguageProvider enableDomTranslation={false}><PortfolioCalculationNotice portfolioId="3" revision={1} /></LanguageProvider>)
  expect(await screen.findByRole('status')).toHaveTextContent('Updating portfolio valuation and holdings')
  view.rerender(<LanguageProvider enableDomTranslation={false}><PortfolioCalculationNotice portfolioId="4" revision={1} /></LanguageProvider>)
  await act(async () => {})
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
})
