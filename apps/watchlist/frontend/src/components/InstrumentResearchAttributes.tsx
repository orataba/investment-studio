import { Fragment, useEffect, useRef, useState } from 'react'

import {
  type InstrumentAttributeDefinition,
  type InstrumentAttributeValuesResponse,
  updateInstrumentAttributes,
} from '../lib/api'
import { formatLabel } from '../lib/format'

type AttributeDomain = InstrumentAttributeDefinition['domain_code']

type Props = {
  instrumentId: string
  attributeValues: InstrumentAttributeValuesResponse | null
  loadError?: string | null
  language: string
  domains?: AttributeDomain[]
  onChange: (value: InstrumentAttributeValuesResponse) => void
  onRetry?: () => void
}

const DOMAIN_META: Partial<Record<AttributeDomain, { title: string; subtitle: string }>> = {
  overview: { title: 'Investment Classification', subtitle: 'Vehicle & Exposure' },
  research: { title: 'Research Framework', subtitle: 'Asset-specific Assessment' },
  monitoring: { title: 'Monitoring Framework', subtitle: 'Ongoing Assessment' },
}

const GROUP_LABELS: Record<string, string> = {
  overview_identity: 'Identity',
  overview_exposure: 'Primary Exposure',
  research_coverage: 'Research Governance',
  research_edge: 'Edge & Philosophy',
  research_process: 'Process Repeatability',
  research_style: 'Style Tags',
  research_manager: 'People & Organization',
  research_risk: 'Risk Management',
  research_terms: 'Capacity, Liquidity & Terms',
  research_governance: 'Governance & Alignment',
  research_delivery: 'Historical Delivery',
  research_role: 'Portfolio Role',
  research_equity: 'Company Research',
  research_etf: 'ETF Research',
  research_index: 'Index Research',
  monitoring_risk: 'Risk Profile',
  monitoring_regime: 'Regime Fit',
  monitoring_operational: 'Operational Coverage',
  custom: 'Custom',
}

function valueList(values: Record<string, unknown>, key: string) {
  const value = values[key]
  if (Array.isArray(value)) {
    return value.map((item) => String(item).trim()).filter(Boolean)
  }
  if (value == null) return []
  const text = String(value).trim()
  return text ? [text] : []
}

function displayValue(value: unknown) {
  if (Array.isArray(value)) {
    const items = value.map((item) => String(item).trim()).filter(Boolean)
    return items.length ? items.join(', ') : '—'
  }
  if (value == null) return '—'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  return String(value).trim() || '—'
}

function rubricText(definition: InstrumentAttributeDefinition) {
  const rubric = definition.rubric_json || {}
  return [
    definition.description,
    typeof rubric.summary === 'string' ? rubric.summary : '',
    typeof rubric.standard === 'string' ? rubric.standard : '',
  ]
    .map((value) => String(value || '').trim())
    .filter(Boolean)
    .join(' ')
}

function applies(
  definition: InstrumentAttributeDefinition,
  values: Record<string, unknown>,
) {
  const entries = Object.entries(definition.applicability_json || {})
  return entries.every(([key, expected]) => {
    if (!expected.length) return true
    const current = valueList(values, key)
    return current.some((value) => expected.includes(value))
  })
}

export default function InstrumentResearchAttributes({
  instrumentId,
  attributeValues,
  loadError = null,
  language,
  domains = ['overview', 'research'],
  onChange,
  onRetry,
}: Props) {
  const pickerRef = useRef<HTMLDivElement | null>(null)
  const [openKey, setOpenKey] = useState<string | null>(null)
  const [savingKey, setSavingKey] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)

  useEffect(() => {
    if (!openKey) return
    const close = (event: MouseEvent) => {
      if (!pickerRef.current?.contains(event.target as Node)) setOpenKey(null)
    }
    window.addEventListener('mousedown', close)
    return () => window.removeEventListener('mousedown', close)
  }, [openKey])

  if (!attributeValues) {
    return (
      <section className="instrument-research-section">
        <div className="instrument-research-section-header">
          <div>
            <div className="panel-title">Research Framework</div>
            <div className="instrument-section-title">Classification & Assessment</div>
          </div>
        </div>
        {loadError ? (
          <div
            className="instrument-placeholder instrument-research-placeholder instrument-framework-load-error"
            role="alert"
          >
            <div>
              <strong>{language === 'zh-Hans' ? '研究字段加载失败' : 'Research fields unavailable'}</strong>
              <span>{loadError}</span>
            </div>
            {onRetry ? (
              <button type="button" onClick={onRetry}>
                {language === 'zh-Hans' ? '重试' : 'Retry'}
              </button>
            ) : null}
          </div>
        ) : (
          <div className="instrument-placeholder instrument-research-placeholder">
            {language === 'zh-Hans' ? '正在加载研究字段…' : 'Loading research fields…'}
          </div>
        )}
      </section>
    )
  }

  const values = {
    ...(attributeValues.taxonomy?.derived_values || {}),
    ...(attributeValues.values || {}),
  }
  const sections = domains.flatMap((domain) => {
    const definitions = attributeValues.definitions
      .filter((definition) => definition.domain_code === domain)
      .filter((definition) => definition.attribute_key !== 'coverage_status')
      .filter(
        (definition) =>
          applies(definition, values) || valueList(values, definition.attribute_key).length > 0,
      )
      .sort(
        (left, right) =>
          left.display_order - right.display_order || left.label.localeCompare(right.label),
      )
    if (!definitions.length) return []
    const groups = definitions.reduce<
      Array<{ code: string; label: string; definitions: InstrumentAttributeDefinition[] }>
    >((items, definition) => {
      const code = definition.group_code || 'custom'
      const group = items.find((item) => item.code === code)
      if (group) group.definitions.push(definition)
      else items.push({ code, label: GROUP_LABELS[code] || formatLabel(code), definitions: [definition] })
      return items
    }, [])
    return [{ domain, groups }]
  })

  if (!sections.length) return null

  async function save(definition: InstrumentAttributeDefinition, value: unknown) {
    setSavingKey(definition.attribute_key)
    setSaveError(null)
    try {
      const response = await updateInstrumentAttributes(instrumentId, {
        values: [{ attribute_key: definition.attribute_key, value }],
      })
      onChange(response)
      if (definition.data_type !== 'multi_select') setOpenKey(null)
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : 'Failed to update research field.')
    } finally {
      setSavingKey(null)
    }
  }

  return (
    <>
      {saveError ? <div className="inline-notice inline-notice-error" role="alert">{saveError}</div> : null}
      {sections.map((section) => {
        const meta = DOMAIN_META[section.domain] || {
          title: 'Research Framework',
          subtitle: formatLabel(section.domain),
        }
        return (
          <section key={section.domain} className="instrument-research-section">
            <div className="instrument-research-section-header">
              <div>
                <div className="panel-title">{meta.title}</div>
                <div className="instrument-section-title">{meta.subtitle}</div>
              </div>
            </div>
            <div className="table-shell instrument-research-table-shell instrument-product-tags-table-shell">
              <table className="terminal-table terminal-table-compact instrument-research-table instrument-product-tags-table">
                <thead>
                  <tr><th>Field</th><th>Value</th></tr>
                </thead>
                <tbody>
                  {section.groups.map((group) => (
                    <Fragment key={`${section.domain}-${group.code}`}>
                      <tr className="instrument-product-tags-group-row">
                        <td colSpan={2}>
                          <div className="instrument-product-tags-group-label">{group.label}</div>
                        </td>
                      </tr>
                      {group.definitions.map((definition) => {
                        const selectedValues = valueList(
                          attributeValues.values,
                          definition.attribute_key,
                        )
                        const help = rubricText(definition)
                        return (
                          <tr key={definition.attribute_key}>
                            <td className="instrument-product-tags-table-label-cell">
                              <div className="instrument-product-tags-field">
                                <span>{definition.label}</span>
                                {help ? (
                                  <span className="instrument-product-tags-help" tabIndex={0}>
                                    ?
                                    <span className="instrument-product-tags-tooltip">{help}</span>
                                  </span>
                                ) : null}
                              </div>
                            </td>
                            <td className="instrument-product-tags-table-value-cell">
                              <div
                                className="instrument-product-tags-picker"
                                ref={openKey === definition.attribute_key ? pickerRef : undefined}
                              >
                                <button
                                  type="button"
                                  className={`instrument-product-tags-picker-trigger${
                                    openKey === definition.attribute_key
                                      ? ' instrument-product-tags-picker-trigger-active'
                                      : ''
                                  }`}
                                  disabled={savingKey === definition.attribute_key}
                                  onClick={() =>
                                    setOpenKey((current) =>
                                      current === definition.attribute_key
                                        ? null
                                        : definition.attribute_key,
                                    )
                                  }
                                >
                                  <span>
                                    {savingKey === definition.attribute_key
                                      ? 'Saving…'
                                      : displayValue(attributeValues.values[definition.attribute_key])}
                                  </span>
                                </button>
                                {openKey === definition.attribute_key ? (
                                  <div className="instrument-product-tags-picker-panel">
                                    <div className="instrument-product-tags-picker-meta">
                                      {definition.data_type === 'multi_select'
                                        ? 'Select one or more'
                                        : 'Select one'}
                                    </div>
                                    <div className="instrument-product-tags-picker-options">
                                      <button
                                        type="button"
                                        className="instrument-product-tags-picker-option"
                                        disabled={savingKey === definition.attribute_key}
                                        onClick={() =>
                                          void save(
                                            definition,
                                            definition.data_type === 'multi_select' ? [] : null,
                                          )
                                        }
                                      >
                                        <span className="instrument-product-tags-picker-check" />
                                        <span className="instrument-product-tags-picker-label">Clear</span>
                                      </button>
                                      {definition.options.map((option) => {
                                        const selected = selectedValues.includes(option)
                                        const nextValue =
                                          definition.data_type === 'multi_select'
                                            ? selected
                                              ? selectedValues.filter((value) => value !== option)
                                              : [...selectedValues, option]
                                            : option
                                        return (
                                          <button
                                            key={option}
                                            type="button"
                                            className={`instrument-product-tags-picker-option${
                                              selected
                                                ? ' instrument-product-tags-picker-option-selected'
                                                : ''
                                            }`}
                                            disabled={savingKey === definition.attribute_key}
                                            onClick={() => void save(definition, nextValue)}
                                          >
                                            <span className="instrument-product-tags-picker-check">
                                              {selected ? '✓' : ''}
                                            </span>
                                            <span className="instrument-product-tags-picker-label">{option}</span>
                                          </button>
                                        )
                                      })}
                                    </div>
                                  </div>
                                ) : null}
                              </div>
                            </td>
                          </tr>
                        )
                      })}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )
      })}
    </>
  )
}
