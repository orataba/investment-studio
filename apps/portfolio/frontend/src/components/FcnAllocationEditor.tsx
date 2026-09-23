import { useState } from 'react'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import { getConcentrationSettings, saveConcentrationSettings, type ConcentrationResponse, type ConcentrationSettingsRecord, type FcnAllocation } from '../lib/concentrationApi'
import { concentrationMessage } from '../lib/concentrationText'

export default function FcnAllocationEditor({ portfolioId, data }: { portfolioId: string; data: ConcentrationResponse }) {
  const zh = useLanguage().language === 'zh-Hans'
  const [settings, setSettings] = useState<ConcentrationSettingsRecord | null>(null)
  const [allocations, setAllocations] = useState<FcnAllocation[]>([])
  const [percentDrafts, setPercentDrafts] = useState<Record<string, Record<string, string>>>({})
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const missingCustomWeight = data.fcn_contracts.some((contract) =>
    allocations.find((item) => item.contract_id === contract.contract_id)?.method === 'custom' &&
    Object.values(percentDrafts[contract.contract_id] ?? {}).some((value) => !value.trim()),
  )
  async function edit() {
    setBusy(true); setError(null)
    try {
      const stored = await getConcentrationSettings(portfolioId, data.as_of_date)
      setSettings(stored); setAllocations(stored.fcn_allocations); setPercentDrafts({}); setOpen(true)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }
  function update(contractId: string, allocation: FcnAllocation) {
    setAllocations((current) => [...current.filter((item) => item.contract_id !== contractId), allocation])
  }
  async function save() {
    if (!settings || missingCustomWeight || busy) return
    setBusy(true); setError(null)
    try {
      await saveConcentrationSettings(portfolioId, {
        expected_revision: settings.latest_revision, effective_from: data.as_of_date,
        enabled_taxonomy_ids: settings.enabled_taxonomy_ids, limits: settings.limits, fcn_allocations: allocations,
      })
      setOpen(false)
      window.dispatchEvent(new CustomEvent('portfolio-concentration-settings-updated', { detail: { portfolioId } }))
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }
  return <div className="concentration-allocation-settings">
    {!open ? <button type="button" onClick={() => void edit()} disabled={busy}>{zh ? '编辑 FCN 本金分配' : 'Edit FCN principal allocation'}</button> : <>
      <p className="portfolio-detail-meta">{zh ? '本金在挂钩标的之间分配一次；这不是 delta 或损失分配。' : 'Principal is allocated once across linked securities; this is not delta or loss allocation.'}</p>
      <fieldset className="concentration-settings-fields" disabled={busy}>
        {data.fcn_contracts.map((contract) => {
          const allocation = allocations.find((item) => item.contract_id === contract.contract_id) ?? { contract_id: contract.contract_id, method: 'equal' as const, weights: [] }
          return <div className="concentration-allocation-contract" key={contract.contract_id}>
            <label className="concentration-field">{contract.name}<select aria-label={`${contract.name} ${zh ? '本金分配' : 'principal allocation'}`} value={allocation.method} onChange={(event) => { setPercentDrafts((current) => ({ ...current, [contract.contract_id]: {} })); update(contract.contract_id, {
              contract_id: contract.contract_id, method: event.target.value as 'equal' | 'custom',
              weights: event.target.value === 'equal' ? [] : contract.underlyings.map((item) => ({ instrument_id: item.instrument_id, weight: 1 / contract.underlyings.length })),
            }) }}><option value="equal">{zh ? '等分本金' : 'Equal principal'}</option><option value="custom">{zh ? '自定义比例' : 'Custom proportions'}</option></select></label>
            {allocation.method === 'custom' ? <div className="concentration-allocation-inputs">{contract.underlyings.map((underlying) => <label className="concentration-field" key={underlying.instrument_id}>{underlying.name}<span className="concentration-percent-input"><input type="number" min="0" max="100" step="any" aria-label={`${contract.name} / ${underlying.name} %`} value={percentDrafts[contract.contract_id]?.[underlying.instrument_id] ?? (allocation.weights.find((item) => item.instrument_id === underlying.instrument_id)?.weight ?? 0) * 100} onChange={(event) => { setPercentDrafts((current) => ({ ...current, [contract.contract_id]: { ...current[contract.contract_id], [underlying.instrument_id]: event.target.value } })); update(contract.contract_id, {
              ...allocation, weights: contract.underlyings.map((item) => ({ instrument_id: item.instrument_id, weight: item.instrument_id === underlying.instrument_id ? Number(event.target.value) / 100 : allocation.weights.find((weight) => weight.instrument_id === item.instrument_id)?.weight ?? 0 })),
            }) }} />%</span></label>)}</div> : null}
          </div>
        })}
      </fieldset>
      {missingCustomWeight ? <p role="alert">{zh ? '请填写每个挂钩标的的分配比例；空白不代表 0%。' : 'Enter an allocation for every underlying; a blank value does not mean 0%.'}</p> : null}
      <div className="concentration-toolbar-actions"><span className="portfolio-detail-meta">{zh ? `分配从 ${data.as_of_date} 起生效` : `Allocation effective from ${data.as_of_date}`}</span><button type="button" onClick={() => { setOpen(false); setError(null) }} disabled={busy}>{zh ? '取消' : 'Cancel'}</button><button type="button" onClick={() => void save()} disabled={busy || missingCustomWeight}>{zh ? '保存本金分配' : 'Save principal allocation'}</button></div>
    </>}
    {error ? <div role="alert" className="inline-notice inline-notice-error">{concentrationMessage(error, zh)}</div> : null}
  </div>
}
