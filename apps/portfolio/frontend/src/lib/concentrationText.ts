const chineseNotes: Record<string, string> = {
  'Account-level holdings are unavailable for concentration. Refresh holdings before retrying.': '集中度计算所需的账户持仓明细暂不可用，请刷新持仓后重试。',
  'Gross direct security market value and remaining FCN principal / current portfolio NAV; options, cash and settlements are excluded from numerators.': '直接证券绝对市值与 FCN 剩余本金 / 组合当前 NAV；期权、现金和待结算款不进入分子。',
  'FCN principal is allocated once per taxonomy. Equal allocation is a management convention, not a loss allocation or risk diversification claim.': '每份 FCN 本金在每套分类中只分配一次。等分是管理约定，不表示损失均分或风险分散。',
  'Direct security limits exclude FCN underlying allocations. FCN limits use remaining contracts × principal per contract, not carrying cost.': '单证券限额仅统计直接持仓。单 FCN 按剩余张数 × 每张名义本金统计，不使用账面成本。',
  'NAV retains the portfolio operating-book valuation basis; FCNs and options have no daily fair-value coverage.': 'NAV 沿用组合现有账面估值口径；FCN 和期权尚无每日公允估值。',
  'Unclassified exposure is retained; classified amounts may be lower bounds.': '未分类敞口仍被保留；已分类金额可能只是下界。',
  'Unclassified exposure may also belong to this group.': '未分类敞口也可能属于此分类，当前仅能确认已知下界。',
  'Taxonomy hierarchy is incomplete or cyclic.': '分类层级不完整或存在循环。',
  'No classification configuration is effective on the holding date.': '持仓日期没有已生效的分类配置。',
  'A positive portfolio NAV is required for concentration weights.': '组合 NAV 必须为正，才能计算集中度占比。',
  'An FCN holding has no contract reference.': '一笔 FCN 持仓缺少合约引用。',
}
export function concentrationMessage(note: string, zh: boolean) {
  if (!zh) return note
  return chineseNotes[note] ?? note
    .replace(/^Missing exposure: (.*)\.$/, '缺少敞口金额：$1。')
    .replace(/^FCN linked securities are missing or ambiguous: (.*)\.$/, 'FCN 挂钩标的缺失或不明确：$1。')
    .replace(/^FCN allocation does not match its linked securities: (.*)\.$/, 'FCN 分配与挂钩标的不一致：$1。')
}
