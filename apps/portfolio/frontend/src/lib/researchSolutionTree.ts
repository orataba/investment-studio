import type { TableCell } from '../../../../../packages/ui/src/tableExport'

export type ResearchSolutionTreeRow = {
  row_id: string
  parent_row_id: string | null
  row_kind: 'portfolio' | 'category' | 'instrument' | 'cash' | 'derivatives'
  member_type: string
  member_id: string
  label: string
  depth: number
  path: string[]
  target_risk_share: number | null
  solved_risk_share: number | null
  current_value_base: number | null
  current_exposure_base: number | null
  current_exposure_weight: number | null
  target_value_base: number | null
  target_weight: number | null
  rebalance_value_base: number | null
  exposure_status: 'complete' | 'unavailable' | 'not_applicable'
  trade_constraint: 'adjustable' | 'no_trade' | 'mixed'
  risk_model_status: 'modeled' | 'excluded' | 'mixed'
  execution_status: 'ready' | 'manual_review_required' | 'no_trade'
  execution_note: string | null
  min_weight: number | null
  max_weight: number | null
  bound_status: string | null
}

export type ResearchSolutionTreeData = {
  schema_version: number
  as_of_date: string | null
  base_currency: string | null
  portfolio_nav: number | null
  capital_weight_basis: 'portfolio_nav' | 'saved_scope'
  risk_attribution_scope: 'portfolio' | 'selected_research_scope'
  hierarchy_status: 'complete' | 'recorded_groups_only'
  configuration_captured_at: string | null
  rows: ResearchSolutionTreeRow[]
}

export function solutionRowLabel(row: ResearchSolutionTreeRow, zh: boolean) {
  if (!zh) return row.label
  if (row.row_kind === 'cash') return '现金'
  if (row.row_kind === 'derivatives') return '衍生品'
  if (row.row_kind === 'portfolio' && ['Portfolio', 'Top Level'].includes(row.label)) return '组合'
  return row.label
}

export function visibleSolutionRows(rows: ResearchSolutionTreeRow[], collapsed: ReadonlySet<string>) {
  const byId = new Map(rows.map((row) => [row.row_id, row]))
  return rows.filter((row) => {
    let parent = row.parent_row_id
    while (parent) {
      if (collapsed.has(parent)) return false
      parent = byId.get(parent)?.parent_row_id ?? null
    }
    return true
  })
}

export function solutionTradeLabel(row: ResearchSolutionTreeRow, zh: boolean) {
  if (row.trade_constraint === 'no_trade') return zh ? '不交易' : 'No trade'
  if (row.rebalance_value_base == null) return '—'
  if (Math.abs(row.rebalance_value_base) < 0.005) return zh ? '不变' : 'Unchanged'
  if (row.row_kind === 'cash') return zh ? '资金变动' : 'Cash change'
  if (row.row_kind === 'portfolio' || row.row_kind === 'category') return zh ? '净调整' : 'Net change'
  return row.rebalance_value_base > 0 ? (zh ? '买入' : 'Buy') : (zh ? '卖出' : 'Sell')
}

export function solutionConstraintLabel(row: ResearchSolutionTreeRow, zh: boolean) {
  const labels = []
  if (row.trade_constraint === 'no_trade') labels.push(zh ? '不交易' : 'No trade')
  if (row.trade_constraint === 'mixed') labels.push(zh ? '部分不交易' : 'Partly no trade')
  if (row.execution_status === 'manual_review_required') labels.push(zh ? '需人工复核' : 'Review required')
  if (row.bound_status && !['within', 'unbounded'].includes(row.bound_status)) labels.push(zh ? '权重边界约束' : 'Weight bound')
  return labels.join(' · ')
}

export function solutionTreeExportRows(tree: ResearchSolutionTreeData, runId: string, status: string, zh: boolean): TableCell[][] {
  const percent = (value: number | null) => value == null ? null : value * 100
  const currency = tree.base_currency ?? (zh ? '币种未保存' : 'Currency not recorded')
  const capitalBasis = tree.capital_weight_basis === 'portfolio_nav' ? 'NAV' : (zh ? '保存范围' : 'Saved scope')
  return [
    zh ? ['层级', '分类路径', '分类 / 标的', '目标风险贡献 (%)', '求解风险贡献 (%)', `账面金额 (${currency})`, '敞口 / NAV (%)', `目标资金权重 / ${capitalBasis} (%)`, `调仓金额 (${currency})`, '方向', '约束', '敞口状态', '资金权重下限 (%)', '资金权重上限 (%)', '数据截止日', '报告币种', '组合 NAV', '风险贡献范围', '配置保存时间', '研究编号', '结果状态', '口径']
      : ['Level', 'Classification path', 'Category / Instrument', 'Target RC (%)', 'Solved RC (%)', `Carrying amount (${currency})`, 'Exposure / NAV (%)', `Target capital weight / ${capitalBasis} (%)`, `Rebalance amount (${currency})`, 'Direction', 'Constraint', 'Exposure status', 'Min capital weight (%)', 'Max capital weight (%)', 'Data cutoff', 'Reporting currency', 'Portfolio NAV', 'Risk attribution scope', 'Configuration captured at', 'Research run', 'Result status', 'Basis'],
    ...tree.rows.map((row) => [row.depth, row.path.join(' / '), solutionRowLabel(row, zh), percent(row.target_risk_share), percent(row.solved_risk_share), row.current_value_base,
      percent(row.current_exposure_weight), percent(row.target_weight), row.rebalance_value_base, solutionTradeLabel(row, zh), solutionConstraintLabel(row, zh),
      row.exposure_status, percent(row.min_weight), percent(row.max_weight), tree.as_of_date, tree.base_currency, tree.portfolio_nav, tree.risk_attribution_scope,
      tree.configuration_captured_at, runId, status,
      zh ? '证券为账户级绝对敞口；FCN本金只计入衍生分支一次；期权敞口不可用；现金不计入敞口。父行汇总直接子行，不与子行重复累加。'
        : 'Securities use account-level gross exposure; FCN principal is counted once in derivatives; option exposure is unavailable; cash is excluded. Parent subtotals must not be added again to their children.',
    ]),
  ]
}
