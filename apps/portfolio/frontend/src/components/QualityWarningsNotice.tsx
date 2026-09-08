import InfoHint from './InfoHint'

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

  const warningLabel =
    visibleWarnings.length === 1
      ? 'Data quality warning'
      : `Data quality warnings (${visibleWarnings.length})`

  return <InfoHint label={warningLabel} detail={visibleWarnings} tone="warning" />
}
