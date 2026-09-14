import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import PortfolioTailRiskPanel from './PortfolioTailRiskPanel'
import type { PortfolioTailRisk } from '../lib/tailRiskApi'

const mocks = vi.hoisted(() => ({ getPortfolioTailRisk: vi.fn(), language: 'en' }))
vi.mock('../lib/tailRiskApi', () => ({ getPortfolioTailRisk: mocks.getPortfolioTailRisk }))
vi.mock('../../../../../packages/ui/src/i18n', () => ({ useLanguage: () => ({ language: mocks.language }) }))

function result(overrides: Partial<PortfolioTailRisk> = {}): PortfolioTailRisk {
  return {
    portfolio_id: 'p', as_of_date: '2026-09-08', base_currency: 'CNY', portfolio_nav: 1000000,
    status: 'available', coverage_status: 'partial', method: 'historical_simulation_current_exposures',
    horizon: 'one_observed_market_session', confidence: .95, lookback_days: 1095,
    window_start_date: '2023-09-09', window_end_date: '2026-09-08',
    history_coverage_status: 'partial', expected_common_observation_count: 750,
    uncovered_observation_count: 250, uncovered_leading_observation_count: 250,
    missing_internal_observation_count: 0, uncovered_trailing_observation_count: 0,
    actual_history_days: 829, history_span_fraction: 829 / 1095,
    first_scenario_start_date: '2024-06-01', last_scenario_end_date: '2026-09-08',
    observation_count: 500, tail_effective_observations: 25, tail_observation_count: 25,
    tail_max_observation_weight: .04, var_amount: 20000, var_nav_fraction: .02,
    expected_shortfall_amount: 30000, expected_shortfall_nav_fraction: .03,
    modeled_gross_exposure: 600000, excluded_gross_exposure: 400000,
    modeled_gross_nav_fraction: .6, excluded_gross_nav_fraction: .4, modeled_fraction_of_known_gross: .6,
    rows: [{ holding_id: 'fcn', instrument_id: null, name: 'FCN contract', market_value_base: 400000,
      weight: .4, status: 'excluded', reason: 'derivative_fair_value_unmodeled', observation_count: 0,
      rejected_period_count: 0, calendar_basis: null, fx_instrument_ids: [],
      fx_rejected_period_count: 0, unmatched_fx_period_count: 0,
      first_scenario_start_date: null, last_scenario_end_date: null }],
    limitations: ['partial_market_risk_coverage'], interpretation: '', precision_note: '', scope_note: '',
    ...overrides,
  }
}

beforeEach(() => { vi.clearAllMocks(); mocks.language = 'en' })

it('shows NAV-denominated paired losses, sample mass, and exclusions without declaring full coverage', async () => {
  mocks.getPortfolioTailRisk.mockResolvedValue(result())
  render(<PortfolioTailRiskPanel portfolioId="p" asOfDate="2026-09-08" />)
  expect(await screen.findByText('2.00%')).toBeInTheDocument()
  expect(screen.getByText('3.00%')).toBeInTheDocument()
  expect(screen.getByText('500 / 25.00')).toBeInTheDocument()
  expect(screen.getByText(/Excluded assets are not zero risk/)).toBeInTheDocument()
  expect(screen.getByText('FCN / Option daily fair values are not modeled')).toBeInTheDocument()
  expect(mocks.getPortfolioTailRisk).toHaveBeenCalledWith('p', { asOfDate: '2026-09-08', confidence: .95, lookbackDays: 1095 })
})

it('clears numbers for a newly selected confidence until that response arrives', async () => {
  let complete: ((value: PortfolioTailRisk) => void) | undefined
  mocks.getPortfolioTailRisk.mockResolvedValueOnce(result()).mockImplementationOnce(() => new Promise((resolve) => { complete = resolve }))
  render(<PortfolioTailRiskPanel portfolioId="p" />)
  await screen.findByText('2.00%')
  fireEvent.change(screen.getByRole('combobox', { name: 'Confidence' }), { target: { value: '.99' } })
  expect(screen.queryByText('2.00%')).not.toBeInTheDocument()
  expect(screen.getByText('Loading historical scenarios…')).toBeInTheDocument()
  await act(async () => { complete?.(result({ confidence: .99, var_nav_fraction: .04 })) })
  expect(await screen.findByText('4.00%')).toBeInTheDocument()
})

it('does not display unresolved tail estimates as zero and translates the reason', async () => {
  mocks.language = 'zh-Hans'
  mocks.getPortfolioTailRisk.mockResolvedValue(result({ status: 'unavailable', observation_count: 90,
    confidence: .99, tail_effective_observations: .9, var_amount: null, var_nav_fraction: null,
    expected_shortfall_amount: null, expected_shortfall_nav_fraction: null,
    limitations: ['less_than_one_tail_observation'] }))
  render(<PortfolioTailRiskPanel portfolioId="p" />)
  expect(await screen.findByText('所选置信度的尾部不足一个观察样本，暂不显示 VaR / ES。')).toBeInTheDocument()
  expect(screen.getByText('90 / 0.90')).toBeInTheDocument()
  expect(screen.queryByText('0.00%')).not.toBeInTheDocument()
  expect(screen.getByText('固定票息票据与期权未纳入日公允价值模型')).toBeInTheDocument()
  expect(screen.getByText(/^固定票息票据与期权未纳入日公允价值模型。/)).toBeInTheDocument()
})

it('ignores a previous portfolios late response after switching portfolio', async () => {
  let oldComplete: ((value: PortfolioTailRisk) => void) | undefined
  mocks.getPortfolioTailRisk.mockImplementationOnce(() => new Promise((resolve) => { oldComplete = resolve }))
    .mockResolvedValueOnce(result({ portfolio_id: 'new', var_nav_fraction: .07 }))
  const page = render(<PortfolioTailRiskPanel portfolioId="old" />)
  page.rerender(<PortfolioTailRiskPanel portfolioId="new" />)
  await screen.findByText('7.00%')
  await act(async () => { oldComplete?.(result({ portfolio_id: 'old', var_nav_fraction: .02 })) })
  expect(screen.getByText('7.00%')).toBeInTheDocument()
  expect(screen.queryByText('2.00%')).not.toBeInTheDocument()
})

it('keeps an actual error visible and supports retry', async () => {
  mocks.getPortfolioTailRisk.mockRejectedValueOnce(new Error('Data unavailable')).mockResolvedValueOnce(result())
  render(<PortfolioTailRiskPanel portfolioId="p" />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Data unavailable')
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  expect(await screen.findByText('2.00%')).toBeInTheDocument()
})

it('keeps the requested years separate from a short actual sample and exposes tail precision', async () => {
  const short = result({
    as_of_date: '2026-09-10', window_start_date: '2023-09-11', window_end_date: '2026-09-10',
    first_scenario_start_date: '2026-06-30', last_scenario_end_date: '2026-09-10',
    observation_count: 52, tail_effective_observations: 2.6, tail_observation_count: 3,
    tail_max_observation_weight: 1 / 2.6, actual_history_days: 72, history_span_fraction: 72 / 1095,
    expected_common_observation_count: 750, uncovered_observation_count: 698,
    uncovered_leading_observation_count: 698,
  })
  mocks.getPortfolioTailRisk.mockResolvedValueOnce(short).mockResolvedValueOnce({
    ...short, lookback_days: 365, window_start_date: '2025-09-10',
  })
  render(<PortfolioTailRiskPanel portfolioId="p" />)
  expect(await screen.findByText('52 / 2.60')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Current holdings historical scenarios' })).toBeInTheDocument()
  expect(screen.getByText('2023-09-11 → 2026-09-10')).toBeInTheDocument()
  expect(screen.getByText('2026-06-30 → 2026-09-10')).toBeInTheDocument()
  expect(screen.getByRole('status')).toHaveTextContent('not the full selected number of years')
  const precision = screen.getByText(/spanning the worst 3 scenarios/)
  expect(precision).toHaveTextContent('38.46%')
  expect(precision.closest('details')).toBeNull()
  expect(screen.getByText(/698 uncovered before the sample, 0 missing within it/)).toBeInTheDocument()
  fireEvent.change(screen.getByRole('combobox', { name: 'Maximum requested lookback' }), { target: { value: '365' } })
  expect(await screen.findByText('2025-09-10 → 2026-09-10')).toBeInTheDocument()
  expect(screen.getByText('52 / 2.60')).toBeInTheDocument()
  expect(screen.getByText('2026-06-30 → 2026-09-10')).toBeInTheDocument()
  expect(mocks.getPortfolioTailRisk).toHaveBeenLastCalledWith('p', { asOfDate: undefined, confidence: .95, lookbackDays: 365 })
})

it('does not invent a missing-session count when the source calendar is unverified', async () => {
  mocks.getPortfolioTailRisk.mockResolvedValue(result({
    history_coverage_status: 'unverified', expected_common_observation_count: null,
    uncovered_observation_count: null, uncovered_leading_observation_count: null,
    missing_internal_observation_count: null, uncovered_trailing_observation_count: null,
  }))
  render(<PortfolioTailRiskPanel portfolioId="p" />)
  expect(await screen.findByRole('status')).toHaveTextContent('full-window coverage cannot be established')
  expect(screen.queryByText(/Verified calendars provide/)).not.toBeInTheDocument()
})
