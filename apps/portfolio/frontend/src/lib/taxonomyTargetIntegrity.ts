import type { PortfolioTargetSetIntegrityIssueRecord } from './api'

export function targetSetIntegrityIssuesForTaxonomy(
  issues: PortfolioTargetSetIntegrityIssueRecord[],
  taxonomyId: string | null | undefined,
) {
  if (!taxonomyId) {
    return []
  }
  return issues.filter((issue) => issue.taxonomy_id === taxonomyId)
}

export function formatTargetSetIntegrityNotice(issues: PortfolioTargetSetIntegrityIssueRecord[]) {
  if (!issues.length) {
    return null
  }

  const previews = issues.slice(0, 3).map(
    (issue) => `${issue.target_set_type.toUpperCase()} · ${issue.scope_label}: ${issue.message}`,
  )
  const remainder = issues.length - previews.length
  const suffix = remainder > 0 ? ` (+${remainder} more)` : ''
  return `${previews.join(' ')}${suffix} Use Edit Targets to complete the active configuration before running Research.`
}
