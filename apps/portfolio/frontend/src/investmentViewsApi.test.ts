import { beforeEach, expect, it, vi } from 'vitest'
const request = vi.hoisted(() => vi.fn(() => Promise.resolve({})))
vi.mock('./lib/api', () => ({ requestPortfolioResource: request }))
import { getInvestmentViews, getInvestmentViewRevisions, correctInvestmentView, selectInvestmentStance } from './lib/investmentViewsApi'

beforeEach(() => request.mockClear())
it('encodes portfolio and note identities and bypasses cached reads', async () => {
  await getInvestmentViews('p/one', 'asset/b&c')
  expect(request).toHaveBeenLastCalledWith('/api/portfolios/p%2Fone/investment-views?instrument_id=asset%2Fb%26c', { signal: undefined, cache: 'no-store' })
  await getInvestmentViewRevisions('p1', 'n/1')
  expect(request).toHaveBeenLastCalledWith('/api/portfolios/p1/investment-views/n%2F1/revisions', { signal: undefined, cache: 'no-store' })
})
it('sends exact correction and stance versions without automatic retries', async () => {
  const draft = { view_date: '2026-10-08', title: '', body: 'Judgment', context: 'Basis', instrument_ids: [], expected_revision: 4 }
  await correctInvestmentView('p1', 'note', draft)
  expect(request).toHaveBeenLastCalledWith('/api/portfolios/p1/investment-views/note', { method: 'PUT', body: JSON.stringify(draft) })
  const selected = { note_id: 'note', revision_number: 3, expected_version: 2 }
  await selectInvestmentStance('p1', selected)
  expect(request).toHaveBeenLastCalledWith('/api/portfolios/p1/investment-views/current-stance', { method: 'PUT', body: JSON.stringify(selected) })
})
