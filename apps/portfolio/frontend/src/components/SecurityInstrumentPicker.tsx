import { useEffect, useMemo, useRef, useState } from 'react'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import { materializePortfolioSecurity, type SecurityCatalogResult, type SecuritySearchOption, type SharedInstrumentRecord } from '../lib/api'
import { useSecurityCatalog } from '../lib/useSecurityCatalog'

export function primaryIdentifier(
  instrument:
    | SecuritySearchOption
    | {
        instrument_id: string
        identifiers: Array<{ identifier_value: string; is_primary: boolean }>
      },
) {
  return (
    instrument.identifiers.find((item) => item.is_primary)?.identifier_value ??
    instrument.identifiers[0]?.identifier_value ??
    instrument.instrument_id
  )
}

export function instrumentSearchLabel(instrument: SecuritySearchOption) {
  return `${primaryIdentifier(instrument)} · ${instrument.instrument_name}`
}

export default function SecurityInstrumentPicker({
  label,
  value,
  instruments,
  onSelect,
  portfolioId,
  onInstrumentRegistered,
  ariaLabel,
}: {
  label: string
  ariaLabel?: string
  value: string
  instruments: SharedInstrumentRecord[]
  onSelect: (instrumentId: string) => void
  portfolioId?: string
  onInstrumentRegistered?: (instrument: SharedInstrumentRecord) => void
}) {
  const { language, t } = useLanguage()
  const text = (en: string, zh: string) => language === 'zh-Hans' ? zh : en
  const [query, setQuery] = useState('')
  const [preparing, setPreparing] = useState(false)
  const [selectionError, setSelectionError] = useState<string | null>(null)
  const generation = useRef(0)
  const preparingRef = useRef(false)
  const callbacks = useRef({ onSelect, onInstrumentRegistered })
  callbacks.current = { onSelect, onInstrumentRegistered }
  useEffect(() => {
    generation.current += 1
    preparingRef.current = false
    setPreparing(false)
    setSelectionError(null)
    return () => { generation.current += 1 }
  }, [portfolioId, value])
  const selectedInstrument =
    instruments.find((instrument) => instrument.instrument_id === value) ?? null
  const inputValue = selectedInstrument
    ? instrumentSearchLabel(selectedInstrument)
    : value || query
  const normalizedQuery = query.trim().toLowerCase()
  const catalog = useSecurityCatalog(portfolioId, query, !value && Boolean(onInstrumentRegistered))
  const results = useMemo(() => {
    if (!normalizedQuery) return []
    const matches = instruments
      .filter((instrument) =>
        [
          instrument.instrument_id,
          ...instrument.identifiers.map(identifier => identifier.identifier_value),
          instrument.instrument_name,
          instrument.instrument_type,
          instrument.currency,
        ]
          .join(' ')
          .toLowerCase()
          .includes(normalizedQuery),
      )
    type Result = { key: string; symbol: string; name: string; detail: string; instrument: SharedInstrumentRecord | null; catalog: SecurityCatalogResult | null }
    const options = new Map<string, Result>(matches.map(instrument => [instrument.instrument_id, {
      key: instrument.instrument_id, symbol: primaryIdentifier(instrument), name: instrument.instrument_name,
      detail: instrument.currency, instrument, catalog: null,
    }]))
    for (const candidate of catalog.results) {
      const existing = instruments.find(instrument => instrument.instrument_id === candidate.existing_instrument_id)
      const key = candidate.existing_instrument_id ?? `${candidate.instrument_type}:${candidate.catalog_provider}:${candidate.catalog_symbol}`
      if (options.has(key)) continue
      options.set(key, {
        key,
        symbol: existing ? primaryIdentifier(existing) : candidate.symbol,
        name: existing?.instrument_name ?? candidate.name,
        detail: existing?.currency ?? `${candidate.exchange_label} · ${candidate.currency}${candidate.currency_verified ? '' : text(' (Currency to be verified)', '（币种待核实）')}`,
        instrument: existing ?? null, catalog: existing ? null : candidate,
      })
    }
    return [...options.values()].sort((left, right) =>
      Number(right.symbol.toLowerCase() === normalizedQuery) - Number(left.symbol.toLowerCase() === normalizedQuery),
    ).slice(0, 12)
  }, [instruments, normalizedQuery, catalog.results, language])
  const showResults = !value && query.trim() !== ''

  async function selectResult(result: typeof results[number]) {
    if (preparingRef.current) return
    setSelectionError(null)
    const registered = result.instrument
    const providerSymbol = registered?.identifiers.find(identifier =>
      identifier.identifier_type === 'provider_symbol' && identifier.identifier_value.startsWith('fmp:'),
    )?.identifier_value.slice(4)
    const needsPrices = portfolioId && onInstrumentRegistered && registered && providerSymbol &&
      ['equity', 'etf'].includes(registered.instrument_type) &&
      !registered.latest_market_data.some(point => point.metric_family === 'price' &&
        ['last', 'close'].includes(point.quote_basis) && point.status === 'complete')
    if (registered && !needsPrices) {
      setQuery('')
      callbacks.current.onSelect(registered.instrument_id)
      return
    }
    const request = result.catalog ?? (needsPrices ? {
      instrument_type: registered.instrument_type as 'equity' | 'etf',
      catalog_provider: 'fmp' as const, catalog_symbol: providerSymbol,
    } : null)
    if (!request || !portfolioId || !onInstrumentRegistered) return
    const selectionGeneration = generation.current
    preparingRef.current = true
    setPreparing(true)
    try {
      const instrument = await materializePortfolioSecurity(portfolioId, request)
      if (selectionGeneration !== generation.current) return
      callbacks.current.onInstrumentRegistered?.(instrument)
      setQuery('')
      callbacks.current.onSelect(instrument.instrument_id)
    } catch (error) {
      if (selectionGeneration === generation.current) {
        setSelectionError(error instanceof Error ? error.message : text('Unable to prepare this security. Select it again to retry.', '证券准备失败，请重新选择后重试。'))
      }
    } finally {
      if (selectionGeneration === generation.current) {
        preparingRef.current = false
        setPreparing(false)
      }
    }
  }

  return (
    <div className="transaction-instrument-search transaction-registry-picker" translate="no">
      <label className="transaction-picker-search">
        <span>{t(label)}</span>
        <input
          type="search"
          aria-label={ariaLabel}
          value={inputValue}
          placeholder={text('Search ticker or name', '搜索代码或名称')}
          onChange={(event) => {
            generation.current += 1
            preparingRef.current = false
            setPreparing(false)
            setSelectionError(null)
            setQuery(event.target.value)
            if (value) {
              onSelect('')
            }
          }}
          onKeyDown={(event) => {
            if (event.key !== 'Enter') return
            event.preventDefault()
            if (showResults && results.length) void selectResult(results[0])
          }}
        />
      </label>
      {showResults ? (
        <div className="transaction-instrument-results">
          {results.map((result) => (
            <button
              type="button"
              key={result.key}
              className="transaction-instrument-result"
              disabled={preparing}
              onClick={() => void selectResult(result)}
            >
              <div className="holding-name-stack">
                <span>{result.symbol}</span>
                <span className="holding-secondary">{result.name}</span>
              </div>
              <span className="transaction-picker-meta">{result.detail}</span>
            </button>
          ))}
          {!results.length && !catalog.loading && !catalog.error ? (
            <div className="transaction-instrument-empty">{text('No matching security.', '没有匹配的证券。')}</div>
          ) : null}
          {catalog.loading && <div role="status" className="transaction-instrument-empty">{text('Searching securities…', '正在搜索证券…')}</div>}
          {preparing && <div role="status" className="transaction-instrument-empty">{text('Preparing security and prices…', '正在登记证券并准备行情…')}</div>}
          {catalog.error && <p role="alert">{catalog.error}</p>}
          {selectionError && <p role="alert">{selectionError}</p>}
        </div>
      ) : null}
    </div>
  )
}
