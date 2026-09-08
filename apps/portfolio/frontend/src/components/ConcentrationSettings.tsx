import { useEffect, useState } from 'react'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'
import {
  concentrationScopeKey, getConcentration, getConcentrationSettings, saveConcentrationSettings,
  type ConcentrationResponse, type ConcentrationRule, type ConcentrationSettingsRecord,
  type ConcentrationScope, type FcnAllocation,
} from '../lib/concentrationApi'
import { localDateIso } from '../lib/riskReturnAlignment'
import { clearPortfolioApiCache } from '../lib/api'
import './portfolio-risk-drawer.css'
import './concentration.css'
import { concentrationMessage } from '../lib/concentrationText'

function newRule(scope: ConcentrationScope, entityId: string | null): ConcentrationRule {
  return { rule_id: crypto.randomUUID(), scope: scope.scope, taxonomy_id: scope.taxonomy_id,
    entity_id: entityId, watch_weight: null, limit_weight: null, enabled: entityId !== null }
}

export function ConcentrationSettings({ portfolioId, taxonomyId, initialScopeKey, canEdit, onSaved }: {
  portfolioId: string
  taxonomyId?: string
  initialScopeKey?: string
  canEdit: boolean
  onSaved?: () => void
}) {
  const { language } = useLanguage()
  const zh = language === 'zh-Hans'
  const [open, setOpen] = useState(false)
  const [saved, setSaved] = useState(false)
  useEffect(() => { if (!saved) return; const timer = setTimeout(() => setSaved(false), 3500); return () => clearTimeout(timer) }, [saved])
  return <>
    <button type="button" className="button-secondary" onClick={() => setOpen(true)}>
      {canEdit ? (zh ? '集中度设置' : 'Concentration settings') : (zh ? '查看集中度设置' : 'View concentration settings')}
    </button>
    {saved ? <span role="status" className="portfolio-detail-meta">{zh ? '已保存' : 'Saved'}</span> : null}
    {open ? <SettingsDrawer key={`${portfolioId}:${taxonomyId ?? ''}`} portfolioId={portfolioId} taxonomyId={taxonomyId} initialScopeKey={initialScopeKey} canEdit={canEdit}
      onClose={() => setOpen(false)} onSaved={() => {
        setSaved(true); setOpen(false); onSaved?.()
        window.dispatchEvent(new CustomEvent('portfolio-concentration-settings-updated', { detail: { portfolioId } }))
      }} /> : null}
  </>
}

function SettingsDrawer({ portfolioId, taxonomyId, initialScopeKey, canEdit, onClose, onSaved }: {
  portfolioId: string; taxonomyId?: string; initialScopeKey?: string; canEdit: boolean; onClose: () => void; onSaved: () => void
}) {
  const { language } = useLanguage()
  const zh = language === 'zh-Hans'
  const text = (en: string, cn: string) => zh ? cn : en
  const dialogRef = useModalDialog(true, onClose)
  const [settings, setSettings] = useState<ConcentrationSettingsRecord | null>(null)
  const [projection, setProjection] = useState<ConcentrationResponse | null>(null)
  const [rules, setRules] = useState<ConcentrationRule[]>([])
  const [allocations, setAllocations] = useState<FcnAllocation[]>([])
  const [selectedScope, setSelectedScope] = useState(taxonomyId ? `taxonomy:${taxonomyId}` : initialScopeKey ?? 'security')
  const [effectiveDate, setEffectiveDate] = useState(localDateIso(new Date()))
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [revision, setRevision] = useState(0)

  useEffect(() => {
    let active = true
    setError(null); setSettings(null)
    Promise.all([getConcentrationSettings(portfolioId), getConcentration(portfolioId)]).then(([value, view]) => {
      if (!active) return
      setSettings(value); setProjection(view); setRules(value.rules); setAllocations(value.fcn_allocations)
      setEffectiveDate(view.as_of_date || localDateIso(new Date()))
    }).catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : String(reason)) })
    return () => { active = false }
  }, [portfolioId, revision])

  const scopes = (projection?.scopes ?? []).filter((scope) => !taxonomyId || scope.taxonomy_id === taxonomyId)
  const scope = scopes.find((item) => concentrationScopeKey(item) === selectedScope) ?? scopes[0]
  const scopeRules = rules.filter((rule) => scope && concentrationScopeKey(rule) === concentrationScopeKey(scope))
  const defaultRule = scopeRules.find((rule) => rule.entity_id === null)
  const overrides = scopeRules.filter((rule) => rule.entity_id !== null)
  function updateRule(rule: ConcentrationRule, patch: Partial<ConcentrationRule>) {
    setRules((current) => current.some((item) => item.rule_id === rule.rule_id)
      ? current.map((item) => item.rule_id === rule.rule_id ? { ...item, ...patch } : item)
      : [...current, { ...rule, ...patch }])
  }
  function thresholdInput(rule: ConcentrationRule, field: 'watch_weight' | 'limit_weight', label: string) {
    return <label className="concentration-field"><span>{label}</span>
      <span className="concentration-percent-input"><input type="number" min="0" step="any" aria-label={label}
        value={rule[field] == null ? '' : Number((rule[field]! * 100).toFixed(8))}
        onChange={(event) => updateRule(rule, { [field]: event.target.value === '' ? null : Number(event.target.value) / 100 })}
        placeholder={text('Not set', '未设置')} />%</span>
    </label>
  }
  const editableDefault = scope ? defaultRule ?? newRule(scope, null) : null
  async function save() {
    if (!settings || !canEdit || saving) return
    if (!effectiveDate) { setError(text('Choose an effective date.', '请选择生效日期。')); return }
    for (const rule of rules) {
      if ([rule.watch_weight, rule.limit_weight].some((value) => value != null && (!Number.isFinite(value) || value < 0))
        || (rule.watch_weight != null && rule.limit_weight != null && rule.watch_weight > rule.limit_weight)) {
        setError(text('Thresholds must be nonnegative, and the watch threshold must not exceed the limit.', '阈值不能为负，关注线不能高于上限。')); return
      }
    }
    for (const allocation of allocations.filter((item) => item.method === 'custom')) {
      if (!allocation.weights.length || allocation.weights.some((item) => !Number.isFinite(item.weight) || item.weight < 0)
        || Math.abs(allocation.weights.reduce((sum, item) => sum + item.weight, 0) - 1) > 1e-9) {
        setError(text('Each FCN custom allocation must sum to 100%.', '每份 FCN 的自定义分配必须合计为 100%。')); return
      }
    }
    setSaving(true); setError(null)
    try {
      await saveConcentrationSettings(portfolioId, { expected_revision: settings.revision, effective_from: effectiveDate, rules, fcn_allocations: allocations })
      onSaved()
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setSaving(false) }
  }
  function updateAllocation(allocation: FcnAllocation) {
    setAllocations((current) => [...current.filter((item) => item.contract_id !== allocation.contract_id), allocation])
  }

  return <div className="portfolio-risk-backdrop" onClick={(event) => { if (event.target === event.currentTarget && !saving) onClose() }}>
    <div ref={dialogRef} className="portfolio-risk-drawer concentration-settings-drawer" role="dialog" aria-modal="true" aria-labelledby="concentration-settings-title" tabIndex={-1}>
      <header className="portfolio-risk-heading"><h1 id="concentration-settings-title">{text('Concentration settings', '集中度设置')}</h1>
        <button type="button" onClick={onClose} disabled={saving}>{text('Close', '关闭')}</button>
      </header>
      <div className="portfolio-risk-body">
        {error ? <div role="alert" className="inline-notice inline-notice-error">{concentrationMessage(error, zh)}
          <button type="button" className="button-secondary" disabled={saving} onClick={() => { clearPortfolioApiCache(); setRevision((value) => value + 1) }}>{text('Reload saved settings', '重新加载已保存设置')}</button>
        </div> : null}
        {!settings && !error ? <p role="status">{text('Loading settings…', '正在加载设置…')}</p> : null}
        {settings ? <>
          <p className="portfolio-detail-meta">{text('Limits use portfolio NAV. Individual rules override scope defaults; limits are separate from allocation and risk contribution targets.', '限额以组合 NAV 为分母。个别规则覆盖通用规则；集中度限额与配置目标、风险贡献目标相互独立。')}</p>
          <label className="concentration-field"><span>{text('Observe', '观察范围')}</span>
            <select value={scope ? concentrationScopeKey(scope) : ''} onChange={(event) => setSelectedScope(event.target.value)}>
              {scopes.map((item) => <option key={concentrationScopeKey(item)} value={concentrationScopeKey(item)}>{item.scope === 'security' ? text('Direct securities', '直接证券') : item.scope === 'fcn' ? text('Single FCN', '单 FCN') : item.name}</option>)}
            </select>
          </label>
          <fieldset disabled={!canEdit || saving} className="concentration-settings-fields">
            {scope && editableDefault ? <>
              <label className="concentration-toggle"><input type="checkbox" checked={defaultRule?.enabled ?? scopeRules.some((rule) => rule.enabled)}
                onChange={(event) => updateRule(editableDefault, { enabled: event.target.checked })} />{text('Enable concentration limits', '开启集中度上限')}</label>
              <p className="portfolio-detail-meta">{text('When disabled, exposure remains visible and alerts for this scope stop. Blank thresholds mean observation only.', '关闭后仍显示敞口，停止该范围的限额提醒。阈值留空表示仅观察。')}</p>
              <div className="concentration-rule-editor">
                <strong>{text('Default for each member', '每个成员的通用限额')}</strong>
                {thresholdInput(editableDefault, 'watch_weight', text('Default watch', '通用关注线'))}
                {thresholdInput(editableDefault, 'limit_weight', text('Default limit', '通用上限'))}
              </div>
              <h2 className="risk-matrix-panel-title">{text('Individual overrides', '个别规则')}</h2>
              {overrides.map((rule) => <div className="concentration-rule-editor" key={rule.rule_id}>
                <strong>{scope.rows.find((row) => row.entity_id === rule.entity_id)?.name ?? rule.entity_id}</strong>
                {thresholdInput(rule, 'watch_weight', `${text('Watch', '关注线')} · ${scope.rows.find((row) => row.entity_id === rule.entity_id)?.name ?? rule.entity_id}`)}
                {thresholdInput(rule, 'limit_weight', `${text('Limit', '上限')} · ${scope.rows.find((row) => row.entity_id === rule.entity_id)?.name ?? rule.entity_id}`)}
                <label className="concentration-toggle"><input type="checkbox" checked={rule.enabled} onChange={(event) => updateRule(rule, { enabled: event.target.checked })} />{text('Enabled', '启用')}</label>
                <button type="button" className="button-secondary" onClick={() => setRules((current) => current.filter((item) => item.rule_id !== rule.rule_id))}>{text('Remove override', '删除个别规则')}</button>
              </div>)}
              <label className="concentration-field"><span>{text('Add an override', '添加个别规则')}</span>
                <select value="" onChange={(event) => { if (event.target.value) setRules((current) => [...current, newRule(scope, event.target.value)]) }}>
                  <option value="">{text('Select a member…', '选择成员…')}</option>
                  {scope.rows.filter((row) => !row.entity_id.startsWith('unassigned:') && !overrides.some((rule) => rule.entity_id === row.entity_id)).map((row) => <option key={row.entity_id} value={row.entity_id}>{`${'　'.repeat(row.depth ?? 0)}${row.name}`}</option>)}
                </select>
              </label>
            </> : <p>{text('No classification is available.', '暂无可用分类。')}</p>}

            {!taxonomyId && projection?.fcn_contracts.length ? <section className="concentration-allocation-settings">
              <h2 className="risk-matrix-panel-title">{text('FCN principal allocation', 'FCN 本金分配')}</h2>
              <p className="portfolio-detail-meta">{text('Equal allocation is a management convention, not equal risk. Each contract sums to 100%; principal is not added to NAV.', '等分属于管理分配约定，不表示风险平均分散。每份合约分配合计为 100%；名义本金不加进 NAV。')}</p>
              {projection.fcn_contracts.map((contract) => {
                const allocation = allocations.find((item) => item.contract_id === contract.contract_id) ?? contract.allocation
                return <div className="concentration-allocation-contract" key={contract.contract_id}>
                  <label className="concentration-field"><strong>{contract.name}</strong><select value={allocation.method}
                    aria-label={`${text('Allocation method', '分配方式')} · ${contract.name}`}
                    onChange={(event) => updateAllocation({ contract_id: contract.contract_id, method: event.target.value as 'equal' | 'custom',
                      weights: event.target.value === 'equal' ? [] : contract.underlyings.map((underlying) => ({ instrument_id: underlying.instrument_id,
                        weight: allocation.weights.find((item) => item.instrument_id === underlying.instrument_id)?.weight ?? 1 / contract.underlyings.length })) })}>
                    <option value="equal">{text('Equal allocation', '等分本金')}</option><option value="custom">{text('Custom allocation', '自定义分配')}</option>
                  </select></label>
                  {allocation.method === 'custom' ? <div className="concentration-allocation-inputs">{contract.underlyings.map((underlying) => <label className="concentration-field" key={underlying.instrument_id}>
                    <span>{underlying.name}</span><span className="concentration-percent-input"><input type="number" min="0" max="100" step="any" aria-label={`${contract.name} · ${underlying.name}`}
                      value={Number(((allocation.weights.find((item) => item.instrument_id === underlying.instrument_id)?.weight ?? 0) * 100).toFixed(8))}
                      onChange={(event) => updateAllocation({ ...allocation, weights: contract.underlyings.map((item) => ({ instrument_id: item.instrument_id, weight: item.instrument_id === underlying.instrument_id ? Number(event.target.value) / 100 : allocation.weights.find((value) => value.instrument_id === item.instrument_id)?.weight ?? 0 })) })} />%</span>
                  </label>)}</div> : null}
                </div>
              })}
            </section> : null}
            <label className="concentration-field"><span>{text('Limits effective date', '限额生效日')}</span><input type="date" value={effectiveDate} onChange={(event) => setEffectiveDate(event.target.value)} /></label>
          </fieldset>
          {canEdit ? <div className="concentration-settings-actions"><button type="button" className="button-primary" onClick={() => void save()} disabled={saving}>{saving ? text('Saving…', '保存中…') : text('Save settings', '保存设置')}</button></div> : null}
        </> : null}
      </div>
    </div>
  </div>
}
