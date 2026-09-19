import { useState } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { LanguageProvider, LANGUAGE_STORAGE_KEY } from '../../../../../packages/ui/src/i18n'
import { materializePortfolioSecurity, searchPortfolioSecurities, type SecurityCatalogResult, type SharedInstrumentRecord } from '../lib/api'
import { instrumentFixture } from '../test/portfolioFixtures'
import SecurityInstrumentPicker from './SecurityInstrumentPicker'

vi.mock('../lib/api', () => ({ materializePortfolioSecurity: vi.fn(), searchPortfolioSecurities: vi.fn() }))

const candidate: SecurityCatalogResult = {
  instrument_type: 'equity', symbol: 'PDD', name: 'PDD Holdings', catalog_provider: 'fmp', catalog_symbol: 'PDD',
  exchange_code: 'XNAS', exchange_label: 'NASDAQ', market: 'US', currency: 'USD', currency_verified: true,
  existing_instrument_id: null,
}
const instrument: SharedInstrumentRecord = {
  ...instrumentFixture({ instrument_id: 'pdd', instrument_type: 'equity', instrument_name: 'PDD Holdings',
    identifiers: [{ identifier_type: 'exchange_ticker', identifier_value: 'PDD', is_primary: true }] }),
  latest_market_data: [], coverage_state: 'complete',
  quote_selection_policy: { trading: [], valuation: [], total_return: [], chart: [], reference: [] },
}

function Picker({ instruments = [], onSelected = vi.fn() }: { instruments?: SharedInstrumentRecord[]; onSelected?: (id: string) => void }) {
  const [records, setRecords] = useState(instruments)
  const [value, setValue] = useState('')
  return <LanguageProvider enableDomTranslation={false}><SecurityInstrumentPicker label="Underlying 1" value={value}
    instruments={records} portfolioId="portfolio-1" onSelect={id => { setValue(id); onSelected(id) }}
    onInstrumentRegistered={record => setRecords(current => [...current.filter(item => item.instrument_id !== record.instrument_id), record])}
  /></LanguageProvider>
}

describe('SecurityInstrumentPicker', () => {
  beforeEach(() => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, 'en')
    vi.mocked(searchPortfolioSecurities).mockReset().mockResolvedValue({ results: [candidate], catalog_errors: {} })
    vi.mocked(materializePortfolioSecurity).mockReset().mockResolvedValue(instrument)
  })

  it('registers only after an explicit result selection and uses the returned canonical ID', async () => {
    const onSelected = vi.fn()
    render(<Picker onSelected={onSelected} />)
    const search = screen.getByRole('searchbox', { name: 'Underlying 1' })
    fireEvent.change(search, { target: { value: 'PDD' } })
    const result = await screen.findByRole('button', { name: /PDD.*NASDAQ.*USD/ })
    expect(materializePortfolioSecurity).not.toHaveBeenCalled()
    expect(onSelected).not.toHaveBeenCalled()
    fireEvent.click(result)
    await waitFor(() => expect(search).toHaveValue('PDD · PDD Holdings'))
    expect(materializePortfolioSecurity).toHaveBeenCalledExactlyOnceWith('portfolio-1', candidate)
    expect(onSelected).toHaveBeenCalledExactlyOnceWith('pdd')
  })

  it('deduplicates catalog aliases by existing ID and selects the registered security without materializing', async () => {
    vi.mocked(searchPortfolioSecurities).mockResolvedValue({ results: [
      { ...candidate, existing_instrument_id: 'pdd' },
      { ...candidate, symbol: 'PDD.A', catalog_symbol: 'PDD.A', existing_instrument_id: 'pdd' },
    ], catalog_errors: {} })
    render(<Picker instruments={[{ ...instrument, instrument_name: 'Custom research name' }]} />)
    const search = screen.getByRole('searchbox', { name: 'Underlying 1' })
    fireEvent.change(search, { target: { value: 'Holdings' } })
    const result = await screen.findByRole('button', { name: /PDD.*Custom research name.*USD/ })
    expect(screen.getAllByRole('button')).toHaveLength(1)
    fireEvent.click(result)
    expect(search).toHaveValue('PDD · Custom research name')
    expect(materializePortfolioSecurity).not.toHaveBeenCalled()
  })

  it('preserves failed searches for an explicit retry', async () => {
    vi.mocked(materializePortfolioSecurity).mockRejectedValueOnce(new Error('Price source is unavailable.'))
    render(<Picker />)
    const search = screen.getByRole('searchbox', { name: 'Underlying 1' })
    fireEvent.change(search, { target: { value: 'PDD' } })
    fireEvent.click(await screen.findByRole('button', { name: /PDD.*NASDAQ/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Price source is unavailable.')
    expect(search).toHaveValue('PDD')
    fireEvent.click(screen.getByRole('button', { name: /PDD.*NASDAQ/ }))
    await waitFor(() => expect(search).toHaveValue('PDD · PDD Holdings'))
  })

  it.each(['search changed', 'picker removed'])('ignores registration after the %s', async (change) => {
    let finish!: (record: SharedInstrumentRecord) => void
    vi.mocked(materializePortfolioSecurity).mockReturnValue(new Promise(resolve => { finish = resolve }))
    const onSelected = vi.fn()
    const view = render(<Picker onSelected={onSelected} />)
    const search = screen.getByRole('searchbox', { name: 'Underlying 1' })
    fireEvent.change(search, { target: { value: 'PDD' } })
    fireEvent.click(await screen.findByRole('button', { name: /PDD.*NASDAQ/ }))
    if (change === 'search changed') fireEvent.change(search, { target: { value: 'QQQ' } })
    else view.unmount()
    await act(async () => { finish(instrument) })
    expect(onSelected).not.toHaveBeenCalled()
    if (change === 'search changed') expect(search).toHaveValue('QQQ')
  })

  it('translates picker labels and messages while retaining source security names', async () => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, 'zh-Hans')
    vi.mocked(searchPortfolioSecurities).mockResolvedValue({ results: [], catalog_errors: {} })
    render(<Picker />)
    const search = screen.getByRole('searchbox', { name: '挂钩标的 1' })
    expect(search).toHaveAttribute('placeholder', '搜索代码或名称')
    fireEvent.change(search, { target: { value: 'PDD' } })
    expect(await screen.findByText('没有匹配的证券。')).toBeInTheDocument()
  })
})
