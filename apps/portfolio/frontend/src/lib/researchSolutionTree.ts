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
  current_weight: number | null
  target_value_base: number | null
  target_weight: number | null
  rebalance_value_base: number | null
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
    zh ? ['层级', '分类路径', '分类 / 标的', '目标风险贡献 (%)', '求解风险贡献 (%)', `账面金额 (${currency})`, '当前权重 (%)', '目标权重 (%)', `调仓金额 (${currency})`, '约束', '资金权重下限 (%)', '资金权重上限 (%)', '数据截止日', '报告币种', '组合 NAV', '求解风险贡献范围', '配置保存时间', '研究编号', '结果状态', '口径']
      : ['Level', 'Classification path', 'Category / Instrument', 'Target RC (%)', 'Solved RC (%)', `Carrying amount (${currency})`, 'Current weight (%)', 'Target weight (%)', `Rebalance amount (${currency})`, 'Constraint', 'Min capital weight (%)', 'Max capital weight (%)', 'Data cutoff', 'Reporting currency', 'Portfolio NAV', 'Solved risk attribution scope', 'Configuration captured at', 'Research run', 'Result status', 'Basis'],
    ...tree.rows.map((row) => [row.depth, row.path.join(' / '), solutionRowLabel(row, zh), percent(row.target_risk_share), percent(row.solved_risk_share), row.current_value_base,
      percent(row.current_weight), percent(row.target_weight), row.rebalance_value_base, solutionConstraintLabel(row, zh),
      percent(row.min_weight), percent(row.max_weight), tree.as_of_date, tree.base_currency, tree.portfolio_nav, tree.risk_attribution_scope,
      tree.configuration_captured_at, runId, status,
      zh ? `当前权重=保存账面金额/全组合NAV；目标权重分母为${capitalBasis}。目标RC只取可证明的组合根预算路径；求解RC汇总保存成员的同范围贡献。父子行不可重复累加。`
        : `Current weight is saved carrying amount / total portfolio NAV; target weight uses ${capitalBasis} capital. Target RC requires a provable portfolio-root budget path; solved RC aggregates saved member contributions within their recorded scope. Parent and child rows are not additive.`,
    ]),
  ]
}
