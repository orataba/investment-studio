import { useState } from 'react'
import HorizontalTableScroll from '../../../../../packages/ui/src/HorizontalTableScroll'
import { downloadXlsx } from '../../../../../packages/ui/src/tableExport'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import type { PortfolioResearchRunRecord } from '../lib/api'
import { formatCurrency, formatNumber, formatPercent } from '../lib/format'
import { solutionConstraintLabel, solutionRowLabel, solutionTreeExportRows, visibleSolutionRows, type ResearchSolutionTreeRow } from '../lib/researchSolutionTree'
import InfoHint from './InfoHint'
import './research-solution-tree.css'

export default function ResearchSolutionTree({ run }: { run: PortfolioResearchRunRecord }) {
  const { language } = useLanguage()
  const zh = language.startsWith('zh')
  const tree = run.detail?.solution_tree
  const [collapseState, setCollapseState] = useState<{ runId: string; ids: Set<string> }>({ runId: run.research_run_id, ids: new Set() })
  const collapsed = collapseState.runId === run.research_run_id ? collapseState.ids : new Set<string>()
  if (!tree?.rows.length) return <div className="empty-state">{zh ? '此研究未保存求解结果。' : 'No saved solution.'}</div>
  const parentIds = new Set(tree.rows.map((row) => row.parent_row_id).filter((value): value is string => Boolean(value)))
  const rows = visibleSolutionRows(tree.rows, collapsed)
  const solve = run.detail?.solve_event
  const status = [run.reliability_state, solve?.target_status].filter(Boolean).join(' / ')
  const setCollapsed = (ids: Set<string>) => setCollapseState({ runId: run.research_run_id, ids })
  const money = (value: number | null) => tree.base_currency ? formatCurrency(value, tree.base_currency) : formatNumber(value, 2)
  const level = (row: ResearchSolutionTreeRow) => row.row_kind === 'portfolio' ? 'root' : row.row_kind === 'instrument' ? 'item' : row.depth === 1 ? 'primary' : 'nested'
  const amountLabel = tree.base_currency ? ` (${tree.base_currency})` : ''
  const globalRisk = ['global_leaf_covariance_v1', 'global_leaf_scalar_targets_v2', 'global_leaf_scalar_targets_v3', 'global_leaf_scalar_targets_v4', 'global_leaf_scalar_targets_v5'].includes(run.detail?.solver_version ?? '')
  const riskDetail = tree.risk_attribution_scope !== 'portfolio'
    ? (zh ? '目标以保存的全组合风险预算为分母；本次只研究指定分类，求解贡献仍以该范围风险为分母，不含外部持仓，两列不能直接计算偏差。' : 'Targets use the saved total portfolio risk budget. This run solves only the selected scope, so solved RC retains that scope’s risk denominator and excludes outside holdings; the two columns cannot be directly subtracted.')
    : !globalRisk
    ? (zh ? '目标仅显示能够从保存的完整预算路径证明的组合风险份额。求解贡献汇总旧记录保存的成员归因，不重新估计协方差；未保存中间预算的目标留空。' : 'Targets show only portfolio risk shares provable from the saved budget path. Solved contributions aggregate archived member attribution without re-estimating covariance; targets with missing intermediate budgets remain blank.')
    : (zh ? '目标从保存的组合根风险预算逐层相乘；资金配置分叉后的风险目标无法据此确定，留空。分类求解贡献汇总成员在同一组合协方差下的贡献。现金与衍生品不进入协方差。' : 'Target RC follows the saved portfolio-root risk-budget path; a branching capital allocation does not determine a risk target. Category solved RC sums member contributions under the same portfolio covariance. Cash and derivatives are excluded from covariance.')
  const capitalDetail = tree.capital_weight_basis === 'portfolio_nav'
    ? (zh ? '当前权重=当前账面金额/保存估值日全组合 NAV；目标权重=求解目标资金金额/NAV。调仓金额=目标金额−当前账面金额；不交易成员保持原金额。' : 'Current weight is current carrying amount / saved total portfolio NAV; target weight is solved target capital / NAV. Rebalance amount is target less current carrying amount; no-trade members retain their amount.')
    : (zh ? '此记录无法证明全组合 NAV，当前权重留空；目标权重保留保存时的研究范围口径。调仓金额来自保存的目标金额和账面金额。' : 'Total portfolio NAV cannot be established from this archive, so current weight is unavailable. Target weights retain the saved research-scope basis; rebalance amounts use the recorded target and carrying amounts.')
  return <div className="research-solution-tree">
    <div className="research-solution-toolbar">
      <div className="research-solution-actions">
        <button type="button" className="table-inline-button" onClick={() => setCollapsed(new Set())}>{zh ? '展开全部' : 'Expand all'}</button>
        <button type="button" className="table-inline-button" onClick={() => setCollapsed(new Set(tree.rows.filter((row) => row.depth > 0 && parentIds.has(row.row_id)).map((row) => row.row_id)))}>{zh ? '收起全部' : 'Collapse all'}</button>
        <button type="button" className="table-inline-button" onClick={() => downloadXlsx(`research-solution-${run.research_run_id}`, solutionTreeExportRows(tree, run.research_run_id, status, zh), zh ? '求解结果' : 'Solution')}>{zh ? '导出 Excel' : 'Export Excel'}</button>
      </div>
    </div>
    <HorizontalTableScroll className="table-shell research-solution-scroll">
      <table className="transactions-table research-solution-table" aria-label={zh ? '研究求解树表' : 'Research solution tree'}>
        <colgroup><col style={{ width: '30%' }} /><col style={{ width: '10%' }} /><col style={{ width: '10%' }} /><col style={{ width: '13%' }} /><col style={{ width: '11%' }} /><col style={{ width: '11%' }} /><col style={{ width: '15%' }} /></colgroup>
        <thead><tr><th>{zh ? '分类 / 标的' : 'Category / Instrument'}</th>
          <th>{zh ? '目标风险贡献' : 'Target RC'}</th><th>{zh ? '求解风险贡献' : 'Solved RC'} <InfoHint label={zh ? '风险贡献口径' : 'Risk contribution basis'} detail={riskDetail} /></th>
          <th>{zh ? '账面金额' : 'Carrying Amount'}{amountLabel}{!tree.base_currency ? <> <InfoHint label={zh ? '报告币种未保存' : 'Reporting currency not recorded'} detail={zh ? '此存档未保存报告币种；账面及调仓金额按原始数值展示，未换算。' : 'This archive did not record its reporting currency. Carrying and rebalance amounts show their original numeric values without conversion.'} /></> : null}</th>
          <th>{zh ? '当前权重' : 'Current Weight'}</th>
          <th>{zh ? '目标权重' : 'Target Weight'} <InfoHint label={zh ? '权重口径' : 'Weight basis'} detail={capitalDetail} /></th><th>{zh ? '调仓金额' : 'Rebalance amount'}{amountLabel}</th></tr></thead>
        <tbody>{rows.map((row) => {
          const constraint = solutionConstraintLabel(row, zh)
          const children = parentIds.has(row.row_id)
          const delta = row.rebalance_value_base
          return <tr key={row.row_id} className="portfolio-tree-row" data-tree-level={level(row)}>
            <td><div className="research-solution-name" style={{ paddingLeft: `${row.depth * 18}px` }}>
              {children ? <button type="button" className="research-solution-toggle" aria-label={`${collapsed.has(row.row_id) ? (zh ? '展开' : 'Expand') : (zh ? '收起' : 'Collapse')} ${row.label}`} aria-expanded={!collapsed.has(row.row_id)} onClick={() => { const next = new Set(collapsed); if (next.has(row.row_id)) next.delete(row.row_id); else next.add(row.row_id); setCollapsed(next) }}><span className={`research-solution-arrow${collapsed.has(row.row_id) ? ' research-solution-arrow-collapsed' : ''}`} /></button> : <span className="research-solution-toggle-space" />}
              <span className="research-solution-label" title={row.path.join(' / ')} translate="no">{solutionRowLabel(row, zh)}</span>
              {constraint ? <span className="research-solution-constraint" title={row.execution_note ?? undefined}>{constraint}</span> : null}
            </div></td>
            <td>{formatPercent(row.target_risk_share)}</td><td>{formatPercent(row.solved_risk_share)}</td><td>{money(row.current_value_base)}</td><td>{formatPercent(row.current_weight)}</td><td>{formatPercent(row.target_weight)}</td>
            <td>{delta == null ? '—' : `${delta > 0.005 ? '+' : delta < -0.005 ? '−' : ''}${money(Math.abs(delta))}`}</td>
          </tr>
        })}</tbody>
      </table>
    </HorizontalTableScroll>
  </div>
}
