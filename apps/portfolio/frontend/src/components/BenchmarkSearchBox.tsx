import { useDeferredValue, useMemo, useState } from 'react'

import type { SharedInstrumentRecord } from '../lib/api'
import { formatLabel } from '../lib/format'

export function instrumentPrimaryIdentifier(instrument: SharedInstrumentRecord) {
  return (
    instrument.identifiers.find((item) => item.is_primary)?.identifier_value ??
    instrument.identifiers[0]?.identifier_value ??
    instrument.asset_id
  )
}

export function benchmarkInstrumentLabel(instrument: SharedInstrumentRecord) {
  return `${instrumentPrimaryIdentifier(instrument)} · ${instrument.asset_name}`
}

function instrumentTypeRank(type: string) {
  return type === 'index' ? 0 : type === 'fund' ? 1 : type === 'etf' ? 2 : type === 'equity' ? 3 : 4
}

type BenchmarkSearchBoxProps = {
  instruments: SharedInstrumentRecord[]
  selectedAssetId: string
  searchValue: string
  onSearchChange: (value: string) => void
  onSelectInstrument: (instrument: SharedInstrumentRecord) => void
  onClear: () => void
  placeholder?: string
  className?: string
}

export default function BenchmarkSearchBox({
  instruments,
  selectedAssetId,
  searchValue,
  onSearchChange,
  onSelectInstrument,
  onClear,
  placeholder = 'Compare...',
  className,
}: BenchmarkSearchBoxProps) {
  const [focused, setFocused] = useState(false)
  const deferredSearch = useDeferredValue(searchValue)
  const selectedInstrument = instruments.find((instrument) => instrument.asset_id === selectedAssetId) ?? null
  const selectedLabel = selectedInstrument ? benchmarkInstrumentLabel(selectedInstrument) : ''
  const inputValue = selectedInstrument && !searchValue ? selectedLabel : searchValue

  const filteredOptions = useMemo(() => {
    const normalizedSearch = deferredSearch.trim().toLowerCase()
    if (!normalizedSearch) {
      return instruments
        .slice()
        .sort(
          (left, right) =>
            instrumentTypeRank(left.asset_type) - instrumentTypeRank(right.asset_type) ||
            instrumentPrimaryIdentifier(left).localeCompare(instrumentPrimaryIdentifier(right)) ||
            left.asset_name.localeCompare(right.asset_name),
        )
        .slice(0, 12)
    }

    return instruments
      .filter((instrument) => {
        const haystack = [
          instrument.asset_name,
          instrument.asset_type,
          instrument.currency,
          instrumentPrimaryIdentifier(instrument),
          benchmarkInstrumentLabel(instrument),
          ...instrument.identifiers.map((identifier) => identifier.identifier_value),
        ]
          .join(' ')
          .toLowerCase()
        return haystack.includes(normalizedSearch)
      })
      .slice(0, 10)
  }, [deferredSearch, instruments])

  return (
    <label className={['overview-benchmark-search', className].filter(Boolean).join(' ') || undefined}>
      <div className="overview-benchmark-search-box">
        <input
          type="search"
          aria-label="Compare benchmark"
          placeholder={placeholder}
          value={inputValue}
          onFocus={() => setFocused(true)}
          onBlur={() => window.setTimeout(() => setFocused(false), 140)}
          onChange={(event) => {
            const nextValue = event.target.value
            if (selectedInstrument && nextValue !== selectedLabel) {
              onClear()
            }
            onSearchChange(nextValue)
          }}
        />
        {selectedInstrument ? (
          <button
            type="button"
            className="overview-benchmark-clear"
            aria-label="Clear benchmark"
            onMouseDown={(event) => event.preventDefault()}
            onClick={onClear}
          >
            ×
          </button>
        ) : (
          <button
            type="button"
            className="overview-benchmark-toggle"
            aria-label="Show benchmark choices"
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => setFocused((current) => !current)}
          >
            <span aria-hidden="true" />
          </button>
        )}
        {focused ? (
          <div className="overview-benchmark-results">
            {filteredOptions.length ? (
              filteredOptions.map((instrument) => (
                <button
                  type="button"
                  key={instrument.asset_id}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => {
                    onSelectInstrument(instrument)
                    setFocused(false)
                  }}
                >
                  <strong>{instrument.asset_name}</strong>
                  <span>
                    {instrumentPrimaryIdentifier(instrument)} · {formatLabel(instrument.asset_type)} · {instrument.currency}
                  </span>
                </button>
              ))
            ) : (
              <div className="overview-benchmark-empty">No database match</div>
            )}
          </div>
        ) : null}
      </div>
    </label>
  )
}
