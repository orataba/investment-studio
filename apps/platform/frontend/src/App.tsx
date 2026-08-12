import { useEffect, useMemo, useRef, useState } from 'react'

import DataOperationsDashboard from './DataOperationsDashboard'
import InstrumentRegistryPage from './InstrumentRegistryPage'
import { instrumentsForVisibility } from './instrumentVisibility'
import {
  type CreateInstrumentPayload,
  fetchJson,
  formatBasisLabel,
  formatFxPairLabel,
  type ImportNavFilePayload,
  type ImportNavTextPayload,
  latestQuoteSnapshot,
  type LifecycleTransitionPayload,
  type PlatformBulkRefreshResponse,
  type PlatformFxRateRecord,
  type PlatformFxRatesResponse,
  type PlatformInstrumentDetail,
  type PlatformInstrumentRecord,
  type PlatformInstrumentsResponse,
  type PlatformRegistrySummary,
  type TriggerChannelRefreshPayload,
  type TriggerRefreshPayload,
  type UpdateSourceSettingsPayload,
  type UpsertFxRatePayload,
  type UpsertMarketDataPayload,
  upsertInstrumentRecord,
} from './instrumentRegistryModel'

const INSTRUMENT_REGISTRY_PATH = '/instruments'

function normalizePath(pathname: string) {
  const normalized = pathname.replace(/\/+$/, '')
  return normalized || '/'
}

export default function App() {
  const currentPath = normalizePath(window.location.pathname)
  const [instruments, setInstruments] = useState<PlatformInstrumentRecord[]>([])
  const [allInstruments, setAllInstruments] = useState<PlatformInstrumentRecord[]>([])
  const [fxRates, setFxRates] = useState<PlatformFxRatesResponse | null>(null)
  const [loadingInstruments, setLoadingInstruments] = useState(false)
  const [registryError, setRegistryError] = useState<string | null>(null)
  const [registryNotice, setRegistryNotice] = useState<string | null>(null)
  const [showInactive, setShowInactive] = useState(false)
  const fxWriteQueueRef = useRef<Promise<void>>(Promise.resolve())
  const registryReadSequenceRef = useRef(0)
  const showInactiveRef = useRef(showInactive)

  showInactiveRef.current = showInactive

  useEffect(() => {
    if (currentPath !== INSTRUMENT_REGISTRY_PATH) return undefined

    let cancelled = false
    const requestSequence = ++registryReadSequenceRef.current
    setLoadingInstruments(true)

    Promise.all([
      fetchJson<PlatformInstrumentsResponse>('/api/instruments?include_inactive=true'),
      fetchJson<PlatformFxRatesResponse>('/api/fx-rates'),
    ])
      .then(([allInstrumentPayload, fxPayload]) => {
        if (!cancelled && requestSequence === registryReadSequenceRef.current) {
          setInstruments(
            instrumentsForVisibility(
              allInstrumentPayload.instruments,
              showInactiveRef.current,
            ),
          )
          setAllInstruments(allInstrumentPayload.instruments)
          setFxRates(fxPayload)
          setRegistryError(null)
        }
      })
      .catch((requestError) => {
        if (!cancelled && requestSequence === registryReadSequenceRef.current) {
          setRegistryError(
            requestError instanceof Error
              ? requestError.message
              : 'Failed to load instruments.',
          )
        }
      })
      .finally(() => {
        if (!cancelled && requestSequence === registryReadSequenceRef.current) {
          setLoadingInstruments(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [currentPath])

  const registrySummary = useMemo<PlatformRegistrySummary>(() => {
    const base = allInstruments.length ? allInstruments : instruments
    const activeCount = base.filter(
      (item) => item.lifecycle_state.status === 'active',
    ).length
    const fundInstruments = base.filter((item) => item.instrument_type === 'fund')
    const indexInstruments = base.filter((item) => item.instrument_type === 'index')
    return {
      total_count: base.length,
      active_count: activeCount,
      archived_count: base.length - activeCount,
      fund_count: fundInstruments.length,
      index_count: indexInstruments.length,
      fund_with_quote_count: fundInstruments.filter(
        (item) => latestQuoteSnapshot(item).latestQuoteDate,
      ).length,
    }
  }, [allInstruments, instruments])

  async function handleCreateInstrument(payload: CreateInstrumentPayload) {
    try {
      const created = await fetchJson<PlatformInstrumentRecord>('/api/instruments', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      setInstruments((current) =>
        upsertInstrumentRecord(current, created, showInactive),
      )
      setAllInstruments((current) => upsertInstrumentRecord(current, created, true))
      setRegistryNotice(`Created instrument “${created.instrument_name}”.`)
      setRegistryError(null)
    } catch (requestError) {
      const message = requestError instanceof Error
        ? requestError.message
        : 'Failed to create instrument.'
      setRegistryError(message)
      throw requestError instanceof Error ? requestError : new Error(message)
    }
  }

  async function handleUpsertFxRate(payload: UpsertFxRatePayload) {
    const previousWrite = fxWriteQueueRef.current
    let finishWrite: () => void = () => undefined
    fxWriteQueueRef.current = new Promise<void>((resolve) => {
      finishWrite = resolve
    })
    await previousWrite.catch(() => undefined)
    try {
      const updated = await fetchJson<PlatformFxRateRecord>('/api/fx-rates', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      const refreshSequence = ++registryReadSequenceRef.current
      const [refreshedAllInstruments, refreshedFxRates] = await Promise.all([
        fetchJson<PlatformInstrumentsResponse>(
          '/api/instruments?include_inactive=true',
        ),
        fetchJson<PlatformFxRatesResponse>('/api/fx-rates'),
      ])
      if (refreshSequence !== registryReadSequenceRef.current) return
      setInstruments(
        instrumentsForVisibility(
          refreshedAllInstruments.instruments,
          showInactiveRef.current,
        ),
      )
      setAllInstruments(refreshedAllInstruments.instruments)
      setFxRates(refreshedFxRates)
      setRegistryNotice(
        `Updated ${formatFxPairLabel(
          updated.base_currency,
          updated.quote_currency,
        )} to ${updated.rate}.`,
      )
      setRegistryError(null)
    } catch (requestError) {
      setRegistryError(
        requestError instanceof Error
          ? requestError.message
          : 'Failed to update FX rate.',
      )
    } finally {
      finishWrite()
    }
  }

  async function handleUpsertMarketData(payload: UpsertMarketDataPayload) {
    try {
      const updated = await fetchJson<PlatformInstrumentRecord>(
        `/api/instruments/${encodeURIComponent(payload.instrument_id)}/market-data`,
        {
          method: 'POST',
          body: JSON.stringify(payload),
        },
      )
      setInstruments((current) =>
        upsertInstrumentRecord(current, updated, showInactive),
      )
      setAllInstruments((current) => upsertInstrumentRecord(current, updated, true))
      setRegistryNotice(
        `Updated ${formatBasisLabel(payload.quote_basis)} for “${updated.instrument_name}”.`,
      )
      setRegistryError(null)
    } catch (requestError) {
      const message = requestError instanceof Error
        ? requestError.message
        : 'Failed to save market data.'
      setRegistryError(message)
      throw requestError instanceof Error ? requestError : new Error(message)
    }
  }

  async function handleUpdateSourceSettings(payload: UpdateSourceSettingsPayload) {
    try {
      const updated = await fetchJson<PlatformInstrumentRecord>(
        `/api/instruments/${encodeURIComponent(payload.instrument_id)}/source-settings`,
        {
          method: 'PUT',
          body: JSON.stringify(payload),
        },
      )
      setInstruments((current) =>
        upsertInstrumentRecord(current, updated, showInactive),
      )
      setAllInstruments((current) => upsertInstrumentRecord(current, updated, true))
      setRegistryNotice(`Saved source settings for “${updated.instrument_name}”.`)
      setRegistryError(null)
    } catch (requestError) {
      const message = requestError instanceof Error
        ? requestError.message
        : 'Failed to save source settings.'
      setRegistryError(message)
      throw requestError instanceof Error ? requestError : new Error(message)
    }
  }

  async function handleTriggerRefresh(payload: TriggerRefreshPayload) {
    try {
      const updated = await fetchJson<PlatformInstrumentRecord>(
        `/api/instruments/${encodeURIComponent(payload.instrument_id)}/refresh`,
        {
          method: 'POST',
          body: JSON.stringify(payload),
        },
      )
      setInstruments((current) =>
        upsertInstrumentRecord(current, updated, showInactive),
      )
      setAllInstruments((current) => upsertInstrumentRecord(current, updated, true))
      setRegistryNotice(
        updated.refresh_status.message ||
          `Triggered refresh for “${updated.instrument_name}”.`,
      )
      setRegistryError(null)
    } catch (requestError) {
      const message = requestError instanceof Error
        ? requestError.message
        : 'Failed to trigger refresh.'
      setRegistryError(message)
      throw requestError instanceof Error ? requestError : new Error(message)
    }
  }

  async function handleTriggerChannelRefresh(payload: TriggerChannelRefreshPayload) {
    try {
      const response = await fetchJson<PlatformBulkRefreshResponse>(
        '/api/instruments/refresh',
        {
          method: 'POST',
          body: JSON.stringify(payload),
        },
      )
      const refreshSequence = ++registryReadSequenceRef.current
      const [refreshedAllInstruments, refreshedFxRates] = await Promise.all([
        fetchJson<PlatformInstrumentsResponse>(
          '/api/instruments?include_inactive=true',
        ),
        fetchJson<PlatformFxRatesResponse>('/api/fx-rates'),
      ])
      if (refreshSequence !== registryReadSequenceRef.current) return
      setInstruments(
        instrumentsForVisibility(
          refreshedAllInstruments.instruments,
          showInactiveRef.current,
        ),
      )
      setAllInstruments(refreshedAllInstruments.instruments)
      setFxRates(refreshedFxRates)
      setRegistryNotice(
        `Refresh ${response.source}: ${response.refreshed_count} updated, ${
          response.results.length - response.refreshed_count
        } checked, ${response.skipped_count} skipped.`,
      )
      setRegistryError(null)
    } catch (requestError) {
      const message = requestError instanceof Error
        ? requestError.message
        : 'Failed to refresh source channel.'
      setRegistryError(message)
      throw requestError instanceof Error ? requestError : new Error(message)
    }
  }

  async function handleImportNavText(payload: ImportNavTextPayload) {
    try {
      const updated = await fetchJson<PlatformInstrumentRecord>(
        `/api/instruments/${encodeURIComponent(payload.instrument_id)}/nav-import`,
        {
          method: 'POST',
          body: JSON.stringify(payload),
        },
      )
      setInstruments((current) =>
        upsertInstrumentRecord(current, updated, showInactive),
      )
      setAllInstruments((current) => upsertInstrumentRecord(current, updated, true))
      setRegistryNotice(
        updated.refresh_status.message ||
          `Imported NAV history for “${updated.instrument_name}”.`,
      )
      setRegistryError(null)
    } catch (requestError) {
      const message = requestError instanceof Error
        ? requestError.message
        : 'Failed to import NAV history.'
      setRegistryError(message)
      throw requestError instanceof Error ? requestError : new Error(message)
    }
  }

  async function handleImportNavFile(payload: ImportNavFilePayload) {
    try {
      const updated = await fetchJson<PlatformInstrumentRecord>(
        `/api/instruments/${encodeURIComponent(payload.instrument_id)}/nav-import/file`,
        {
          method: 'POST',
          body: JSON.stringify(payload),
        },
      )
      setInstruments((current) =>
        upsertInstrumentRecord(current, updated, showInactive),
      )
      setAllInstruments((current) => upsertInstrumentRecord(current, updated, true))
      setRegistryNotice(
        updated.refresh_status.message ||
          `Imported NAV history for “${updated.instrument_name}”.`,
      )
      setRegistryError(null)
    } catch (requestError) {
      const message = requestError instanceof Error
        ? requestError.message
        : 'Failed to import NAV file.'
      setRegistryError(message)
      throw requestError instanceof Error ? requestError : new Error(message)
    }
  }

  async function handleArchiveInstrument(payload: LifecycleTransitionPayload) {
    try {
      const updated = await fetchJson<PlatformInstrumentRecord>(
        `/api/instruments/${encodeURIComponent(payload.instrument_id)}/archive`,
        {
          method: 'POST',
          body: JSON.stringify(payload),
        },
      )
      setInstruments((current) =>
        upsertInstrumentRecord(current, updated, showInactive),
      )
      setAllInstruments((current) => upsertInstrumentRecord(current, updated, true))
      setRegistryNotice(
        `Archived “${updated.instrument_name}”. Default downstream discovery now hides it.`,
      )
      setRegistryError(null)
    } catch (requestError) {
      const message = requestError instanceof Error
        ? requestError.message
        : 'Failed to archive instrument.'
      setRegistryError(message)
      throw requestError instanceof Error ? requestError : new Error(message)
    }
  }

  async function handleRestoreInstrument(payload: LifecycleTransitionPayload) {
    try {
      const updated = await fetchJson<PlatformInstrumentRecord>(
        `/api/instruments/${encodeURIComponent(payload.instrument_id)}/restore`,
        {
          method: 'POST',
          body: JSON.stringify(payload),
        },
      )
      setInstruments((current) =>
        upsertInstrumentRecord(current, updated, showInactive),
      )
      setAllInstruments((current) => upsertInstrumentRecord(current, updated, true))
      setRegistryNotice(`Restored “${updated.instrument_name}” to downstream discovery.`)
      setRegistryError(null)
    } catch (requestError) {
      const message = requestError instanceof Error
        ? requestError.message
        : 'Failed to restore instrument.'
      setRegistryError(message)
      throw requestError instanceof Error ? requestError : new Error(message)
    }
  }

  async function handleFundNavMutationCommitted(instrumentId: string) {
    try {
      const detail = await fetchJson<PlatformInstrumentDetail>(
        `/api/instruments/${encodeURIComponent(instrumentId)}`,
      )
      setInstruments((current) =>
        upsertInstrumentRecord(current, detail, showInactive),
      )
      setAllInstruments((current) => upsertInstrumentRecord(current, detail, true))
      setRegistryError(null)
      return detail
    } catch (requestError) {
      setRegistryError(
        requestError instanceof Error
          ? requestError.message
          : 'The NAV event was confirmed, but the refreshed instrument failed to load.',
      )
      throw requestError
    }
  }

  if (currentPath === INSTRUMENT_REGISTRY_PATH) {
    return (
      <InstrumentRegistryPage
        instruments={instruments}
        registrySummary={registrySummary}
        fxRates={fxRates}
        loading={loadingInstruments}
        error={registryError}
        notice={registryNotice}
        showInactive={showInactive}
        onToggleShowInactive={() =>
          setShowInactive((current) => {
            const next = !current
            setInstruments(instrumentsForVisibility(allInstruments, next))
            return next
          })
        }
        onCreateInstrument={handleCreateInstrument}
        onUpsertFxRate={handleUpsertFxRate}
        onUpsertMarketData={handleUpsertMarketData}
        onUpdateSourceSettings={handleUpdateSourceSettings}
        onTriggerRefresh={handleTriggerRefresh}
        onTriggerChannelRefresh={handleTriggerChannelRefresh}
        onImportNavText={handleImportNavText}
        onImportNavFile={handleImportNavFile}
        onFundNavMutationCommitted={handleFundNavMutationCommitted}
        onArchiveInstrument={handleArchiveInstrument}
        onRestoreInstrument={handleRestoreInstrument}
      />
    )
  }

  return <DataOperationsDashboard />
}
