export function normalizeQualityWarnings(warnings: readonly string[] | null | undefined) {
  return [...new Set((warnings ?? []).map((warning) => warning.trim()).filter(Boolean))]
}

export default function QualityWarningsNotice({
  warnings,
}: {
  warnings: readonly string[] | null | undefined
}) {
  const visibleWarnings = normalizeQualityWarnings(warnings)

  if (!visibleWarnings.length) {
    return null
  }

  const warningDetail = visibleWarnings.join(' ')
  const missingMarketDataWarnings = visibleWarnings.filter((warning) => warning.startsWith('Required market data missing: '))
  const warningLabel =
    visibleWarnings.length === 1
      ? 'Data quality warning'
      : `Data quality warnings (${visibleWarnings.length})`

  return (
    <div
      className="inline-notice inline-notice-warning"
      role="status"
      title={warningDetail}
      aria-label={`${warningLabel}. ${warningDetail}`}
      tabIndex={0}
    >
      {warningLabel} <span aria-hidden="true">ⓘ</span>
      {missingMarketDataWarnings.map((warning) => <div key={warning}>{warning}</div>)}
    </div>
  )
}
