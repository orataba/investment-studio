import { describe, expect, it } from 'vitest'

import type { PortfolioTargetSetIntegrityIssueRecord } from './lib/api'
import {
  formatTargetSetIntegrityNotice,
  targetSetIntegrityIssuesForTaxonomy,
} from './lib/taxonomyTargetIntegrity'

const issues: PortfolioTargetSetIntegrityIssueRecord[] = [
  {
    taxonomy_id: 'tax-risk',
    comparator_taxonomy_node_id: 'tax-risk-absolute',
    scope_label: 'Absolute Return',
    target_set_id: 'tax-risk-saa-absolute',
    target_set_type: 'saa',
    target_set_name: 'Absolute Return SAA',
    issue_code: 'invalid_active_target_set',
    message: 'Target set lines must cover every direct member in the selected scope.',
  },
  {
    taxonomy_id: 'tax-other',
    comparator_taxonomy_node_id: null,
    scope_label: 'Top Level',
    target_set_id: 'tax-other-taa-root',
    target_set_type: 'taa',
    target_set_name: 'Other Root TAA',
    issue_code: 'invalid_active_target_set',
    message: 'Non-cash target_risk_share values must sum to 100%.',
  },
]

describe('taxonomy target-set integrity presentation', () => {
  it('only exposes issues for the selected taxonomy', () => {
    expect(targetSetIntegrityIssuesForTaxonomy(issues, 'tax-risk')).toEqual([issues[0]])
    expect(targetSetIntegrityIssuesForTaxonomy(issues, null)).toEqual([])
  })

  it('identifies the target kind and scope and directs the user to Edit Targets', () => {
    const notice = formatTargetSetIntegrityNotice([issues[0]])
    expect(notice).toContain('SAA · Absolute Return')
    expect(notice).toContain('Edit Targets')
    expect(notice).toContain('before running Allocation Research')
    expect(formatTargetSetIntegrityNotice([])).toBeNull()
  })
})
