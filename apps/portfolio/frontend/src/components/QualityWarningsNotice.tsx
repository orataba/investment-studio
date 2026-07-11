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

  return (
    <div className="inline-notice inline-notice-warning" role="status">
      {visibleWarnings.length === 1 ? (
        visibleWarnings[0]
      ) : (
        <ul>
          {visibleWarnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
