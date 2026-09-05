import { useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'

import type { SharedInstrumentRecord } from '../lib/api'
import { formatLabel } from '../lib/format'

export function instrumentPrimaryIdentifier(instrument: SharedInstrumentRecord) {
  return (
    instrument.identifiers.find((item) => item.is_primary)?.identifier_value ??
    instrument.identifiers[0]?.identifier_value ??
    instrument.instrument_id
  )
}

export function benchmarkInstrumentLabel(instrument: SharedInstrumentRecord) {
  return `${instrumentPrimaryIdentifier(instrument)} · ${instrument.instrument_name}`
}

function instrumentTypeRank(type: string) {
  return type === 'index'
    ? 0
    : type === 'public_fund'
      ? 1
      : type === 'private_fund'
        ? 2
        : type === 'etf'
          ? 3
          : type === 'equity'
            ? 4
            : 5
}

type BenchmarkSearchBoxProps = {
  instruments: SharedInstrumentRecord[]
  selectedInstrumentId: string
  searchValue: string
  onSearchChange: (value: string) => void
  onSelectInstrument: (instrument: SharedInstrumentRecord) => void
  onClear: () => void
  placeholder?: string
  className?: string
}

export default function BenchmarkSearchBox({
  instruments,
  selectedInstrumentId,
  searchValue,
  onSearchChange,
  onSelectInstrument,
  onClear,
  placeholder = 'Compare...',
  className,
}: BenchmarkSearchBoxProps) {
  const [focused, setFocused] = useState(false)
  const rootRef = useRef<HTMLDivElement | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const deferredSearch = useDeferredValue(searchValue)
  const selectedInstrument = instruments.find((instrument) => instrument.instrument_id === selectedInstrumentId) ?? null
  const selectedLabel = selectedInstrument ? benchmarkInstrumentLabel(selectedInstrument) : ''
  const inputValue = selectedInstrument && !searchValue ? selectedLabel : searchValue

  useEffect(() => {
    if (!focused) {
      return undefined
    }

    function handleDocumentPointerDown(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) {
        setFocused(false)
      }
    }

    document.addEventListener('pointerdown', handleDocumentPointerDown)
    return () => document.removeEventListener('pointerdown', handleDocumentPointerDown)
  }, [focused])

  const filteredOptions = useMemo(() => {
    const normalizedSearch = deferredSearch.trim().toLowerCase()
    if (!normalizedSearch) {
      return instruments
        .slice()
        .sort(
          (left, right) =>
            instrumentTypeRank(left.instrument_type) - instrumentTypeRank(right.instrument_type) ||
            instrumentPrimaryIdentifier(left).localeCompare(instrumentPrimaryIdentifier(right)) ||
            left.instrument_name.localeCompare(right.instrument_name),
        )
        .slice(0, 12)
    }

    return instruments
      .filter((instrument) => {
        const haystack = [
          instrument.instrument_name,
          instrument.instrument_type,
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
    <div className={['overview-benchmark-search', className].filter(Boolean).join(' ') || undefined} ref={rootRef}>
      <div className="overview-benchmark-search-box">
        <input
          ref={inputRef}
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
                  key={instrument.instrument_id}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={(event) => {
                    if (event.detail > 0) inputRef.current?.blur()
                    onSelectInstrument(instrument)
                    setFocused(false)
                  }}
                >
                  <strong translate="no">{instrument.instrument_name}</strong>
                  <span>
                    {instrumentPrimaryIdentifier(instrument)} · {formatLabel(instrument.instrument_type)} · {instrument.currency}
                  </span>
                </button>
              ))
            ) : (
              <div className="overview-benchmark-empty">No database match</div>
            )}
          </div>
        ) : null}
      </div>
    </div>
  )
}
