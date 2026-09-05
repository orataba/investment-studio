import { useDeferredValue, useMemo, useState } from 'react'
import type { SecuritySearchOption, SharedInstrumentRecord } from '../lib/api'

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

export default function RegistryInstrumentPicker({
  label,
  value,
  instruments,
  onSelect,
}: {
  label: string
  value: string
  instruments: SharedInstrumentRecord[]
  onSelect: (instrumentId: string) => void
}) {
  const [query, setQuery] = useState('')
  const deferredQuery = useDeferredValue(query)
  const selectedInstrument =
    instruments.find((instrument) => instrument.instrument_id === value) ?? null
  const inputValue = selectedInstrument
    ? instrumentSearchLabel(selectedInstrument)
    : query
  const normalizedQuery = deferredQuery.trim().toLowerCase()
  const results = useMemo(() => {
    if (!normalizedQuery) {
      return []
    }
    return instruments
      .filter((instrument) =>
        [
          primaryIdentifier(instrument),
          instrument.instrument_name,
          instrument.instrument_type,
          instrument.currency,
        ]
          .join(' ')
          .toLowerCase()
          .includes(normalizedQuery),
      )
      .slice(0, 12)
  }, [instruments, normalizedQuery])
  const showResults = !selectedInstrument && query.trim() !== ''

  function selectResult(instrument: SharedInstrumentRecord) {
    setQuery('')
    onSelect(instrument.instrument_id)
  }

  return (
    <div className="transaction-instrument-search transaction-registry-picker">
      <label className="transaction-picker-search">
        <span>{label}</span>
        <input
          type="search"
          value={inputValue}
          placeholder="Search ticker or name"
          onChange={(event) => {
            setQuery(event.target.value)
            if (selectedInstrument) {
              onSelect('')
            }
          }}
          onKeyDown={(event) => {
            if (event.key !== 'Enter' || results.length === 0) {
              return
            }
            event.preventDefault()
            selectResult(results[0])
          }}
        />
      </label>
      {showResults ? (
        <div className="transaction-instrument-results">
          {results.map((instrument) => (
            <button
              type="button"
              key={instrument.instrument_id}
              className="transaction-instrument-result"
              onClick={() => selectResult(instrument)}
            >
              <div className="holding-name-stack">
                <span>{primaryIdentifier(instrument)}</span>
                <span className="holding-secondary" translate="no">{instrument.instrument_name}</span>
              </div>
              <span className="transaction-picker-meta">{instrument.currency}</span>
            </button>
          ))}
          {!results.length ? (
            <div className="transaction-instrument-empty">No matching registered security.</div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
