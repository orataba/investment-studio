import { useDeferredValue, useEffect, useId, useMemo, useRef, useState } from 'react'

import type { SharedInstrumentRecord } from '../lib/api'

export function instrumentPrimaryIdentifier(instrument: SharedInstrumentRecord) {
  return (
    instrument.identifiers.find((identifier) => identifier.is_primary)?.identifier_value ??
    instrument.identifiers[0]?.identifier_value ??
    instrument.instrument_id
  )
}

export function instrumentFilterLabel(instrument: SharedInstrumentRecord) {
  return `${instrumentPrimaryIdentifier(instrument)} · ${instrument.instrument_name}`
}

export function rankInstrumentMatches(
  instruments: SharedInstrumentRecord[],
  query: string,
  limit = 10,
) {
  const normalizedQuery = query.trim().toLocaleLowerCase()
  if (!normalizedQuery) {
    return []
  }

  return instruments
    .map((instrument) => {
      const identifier = instrumentPrimaryIdentifier(instrument)
      const normalizedIdentifier = identifier.toLocaleLowerCase()
      const normalizedName = instrument.instrument_name.toLocaleLowerCase()
      const normalizedType = instrument.instrument_type.toLocaleLowerCase()
      const normalizedCurrency = instrument.currency.toLocaleLowerCase()
      let rank = Number.POSITIVE_INFINITY
      if (normalizedIdentifier === normalizedQuery) {
        rank = 0
      } else if (normalizedIdentifier.startsWith(normalizedQuery)) {
        rank = 1
      } else if (normalizedName.startsWith(normalizedQuery)) {
        rank = 2
      } else if (normalizedIdentifier.includes(normalizedQuery)) {
        rank = 3
      } else if (normalizedName.includes(normalizedQuery)) {
        rank = 4
      } else if (`${normalizedType} ${normalizedCurrency}`.includes(normalizedQuery)) {
        rank = 5
      }
      return { instrument, rank, identifier }
    })
    .filter((candidate) => Number.isFinite(candidate.rank))
    .sort(
      (left, right) =>
        left.rank - right.rank ||
        left.identifier.localeCompare(right.identifier) ||
        left.instrument.instrument_id.localeCompare(right.instrument.instrument_id),
    )
    .slice(0, Math.max(0, limit))
    .map((candidate) => candidate.instrument)
}

type InstrumentFilterComboboxProps = {
  instruments: SharedInstrumentRecord[]
  value: string
  disabled?: boolean
  onChange: (instrumentId: string) => void
}

export default function InstrumentFilterCombobox({
  instruments,
  value,
  disabled = false,
  onChange,
}: InstrumentFilterComboboxProps) {
  const listboxId = useId()
  const rootRef = useRef<HTMLDivElement | null>(null)
  const selectedInstrument = useMemo(
    () => instruments.find((instrument) => instrument.instrument_id === value) ?? null,
    [instruments, value],
  )
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(0)
  const deferredQuery = useDeferredValue(query)
  const matches = useMemo(
    () => rankInstrumentMatches(instruments, deferredQuery),
    [deferredQuery, instruments],
  )
  const inputValue = open ? query : selectedInstrument ? instrumentFilterLabel(selectedInstrument) : ''

  useEffect(() => {
    setQuery('')
    setActiveIndex(0)
  }, [value])

  function selectInstrument(instrument: SharedInstrumentRecord) {
    onChange(instrument.instrument_id)
    setQuery('')
    setOpen(false)
    setActiveIndex(0)
  }

  return (
    <div
      ref={rootRef}
      className="transaction-instrument-filter"
      onBlur={(event) => {
        if (!rootRef.current?.contains(event.relatedTarget as Node | null)) {
          setOpen(false)
          setQuery('')
        }
      }}
    >
      <input
        type="search"
        className="transaction-filter-input"
        role="combobox"
        aria-label="Instrument"
        aria-autocomplete="list"
        aria-expanded={open && Boolean(query.trim())}
        aria-controls={listboxId}
        aria-activedescendant={
          open && matches[activeIndex]
            ? `${listboxId}-${matches[activeIndex].instrument_id}`
            : undefined
        }
        disabled={disabled}
        value={inputValue}
        placeholder="All instruments"
        onFocus={() => {
          setOpen(true)
          setQuery('')
          setActiveIndex(0)
        }}
        onChange={(event) => {
          setOpen(true)
          setQuery(event.target.value)
          setActiveIndex(0)
          if (!event.target.value && value) {
            onChange('')
          }
        }}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            setOpen(false)
            setQuery('')
            return
          }
          if (!matches.length) {
            return
          }
          if (event.key === 'ArrowDown') {
            event.preventDefault()
            setOpen(true)
            setActiveIndex((current) => Math.min(current + 1, matches.length - 1))
            return
          }
          if (event.key === 'ArrowUp') {
            event.preventDefault()
            setOpen(true)
            setActiveIndex((current) => Math.max(current - 1, 0))
            return
          }
          if (event.key === 'Enter' && open) {
            event.preventDefault()
            selectInstrument(matches[activeIndex])
          }
        }}
      />
      {selectedInstrument && !open ? (
        <button
          type="button"
          className="transaction-instrument-filter-clear"
          aria-label="Clear instrument filter"
          onClick={() => onChange('')}
        >
          ×
        </button>
      ) : null}
      {open && query.trim() ? (
        <div id={listboxId} className="transaction-instrument-filter-results" role="listbox">
          {matches.map((instrument, index) => (
            <button
              id={`${listboxId}-${instrument.instrument_id}`}
              key={instrument.instrument_id}
              type="button"
              role="option"
              aria-selected={index === activeIndex}
              className={`transaction-instrument-filter-option ${
                index === activeIndex ? 'transaction-instrument-filter-option-active' : ''
              }`}
              onMouseDown={(event) => event.preventDefault()}
              onMouseEnter={() => setActiveIndex(index)}
              onClick={() => selectInstrument(instrument)}
            >
              <span className="holding-name-stack">
                <strong>{instrumentPrimaryIdentifier(instrument)}</strong>
                <span className="holding-secondary">{instrument.instrument_name}</span>
              </span>
              <span className="transaction-picker-meta">
                {instrument.instrument_type} · {instrument.currency}
              </span>
            </button>
          ))}
          {!matches.length ? (
            <div className="transaction-instrument-empty">No matching instrument.</div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
