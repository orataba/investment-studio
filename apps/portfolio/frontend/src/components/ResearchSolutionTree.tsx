import { useState } from 'react'
import HorizontalTableScroll from '../../../../../packages/ui/src/HorizontalTableScroll'
import { downloadXlsx } from '../../../../../packages/ui/src/tableExport'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import type { PortfolioResearchRunRecord } from '../lib/api'
import { formatCurrency, formatNumber, formatPercent } from '../lib/format'
import { solutionConstraintLabel, solutionRowLabel, solutionTradeLabel, solutionTreeExportRows, visibleSolutionRows, type ResearchSolutionTreeRow } from '../lib/researchSolutionTree'
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
  const globalRisk = ['global_leaf_covariance_v1', 'global_leaf_scalar_targets_v2', 'global_leaf_scalar_targets_v3', 'global_leaf_scalar_targets_v4'].includes(run.detail?.solver_version ?? '')
  const riskDetail = !globalRisk
    ? (zh ? '旧研究按保存时的模型展示风险目标与贡献，局部目标不一定与全组合贡献同口径；重新运行才使用当前全局求解。' : 'This archived run retains its original risk model. Local targets may differ in scope from total portfolio contributions; rerun to use the current global solver.')
    : tree.risk_attribution_scope === 'portfolio'
    ? (zh ? '目标与求解风险贡献均以本次研究全组合风险为分母。现金和衍生品不进入协方差，不代表它们没有经济风险。' : 'Target and solved RC use this run’s total portfolio risk. Cash and derivatives are excluded from covariance, which does not establish zero economic risk.')
    : (zh ? '此研究只覆盖保存的所选分类；风险贡献分母为该范围风险，不包含外部持仓。资金权重与敞口仍使用保存的全组合 NAV。' : 'RC covers only the saved selected research scope, excluding outside holdings. Capital weights and exposures still use the saved total portfolio NAV.')
  const exposureDetail = zh
    ? '证券使用账户级绝对敞口，FCN本金只计入衍生分支一次；不在证券分类中重复穿透分配。期权未建模敞口及旧存档未保存敞口显示 —；现金不计入敞口。父子行不可累加。'
    : 'Securities use account-level gross exposure. FCN principal appears once in derivatives, without a second allocation to security categories. Unmodeled option exposure and unrecorded archived exposure show —; cash is excluded. Parent and child rows are not additive.'
  const capitalDetail = tree.capital_weight_basis === 'portfolio_nav'
    ? (zh ? '目标资金金额除以本次估值日的全组合 NAV；调仓金额为目标资金金额减当前账面金额。不交易成员保留账面金额。' : 'Target capital divided by this valuation date’s total portfolio NAV. Rebalance amount is target capital less current carrying amount. No-trade members retain their carrying amount.')
    : (zh ? '旧记录没有保存全组合 NAV，权重保留保存时的研究范围口径；没有重新计算。调仓金额来自保存的目标金额和账面金额。' : 'This archive did not record total portfolio NAV. Weights retain the saved research-scope basis without recalculation; rebalance amounts use the recorded target and carrying amounts.')
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
          <th>{zh ? '敞口 / NAV' : 'Exposure / NAV'} <InfoHint label={zh ? '敞口口径' : 'Exposure basis'} detail={exposureDetail} /></th>
          <th>{zh ? '目标资金权重' : 'Target Capital Weight'} <InfoHint label={zh ? '资金权重口径' : 'Capital weight basis'} detail={capitalDetail} /></th><th>{zh ? '调仓金额' : 'Rebalance amount'}{amountLabel}</th></tr></thead>
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
            <td>{formatPercent(row.target_risk_share)}</td><td>{formatPercent(row.solved_risk_share)}</td><td>{money(row.current_value_base)}</td><td>{formatPercent(row.current_exposure_weight)}</td><td>{formatPercent(row.target_weight)}</td>
            <td><span>{delta == null ? '—' : `${delta > 0.005 ? '+' : delta < -0.005 ? '−' : ''}${money(Math.abs(delta))}`}</span>{delta != null ? <span className="research-solution-direction">{solutionTradeLabel(row, zh)}</span> : null}</td>
          </tr>
        })}</tbody>
      </table>
    </HorizontalTableScroll>
  </div>
}
