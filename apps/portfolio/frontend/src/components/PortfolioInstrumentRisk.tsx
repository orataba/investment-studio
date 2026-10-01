import { useState } from 'react'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import RiskPanel from '../../../../../packages/ui/src/InstrumentRiskPanel'
import type { ResearchAssistantReference } from '../../../../../packages/ui/src/researchReference'
import {
  type RiskWorkspace,
  percent,
} from '../../../../../packages/ui/src/instrumentRisk'
import '../../../../../packages/ui/src/instrument-risk.css'
import {
  requestInstrumentRisk,
  type HoldingsWorkspaceResponse,
} from '../lib/api'
import {
  buildWatchlistInstrumentDetailUrl,
} from '../lib/navigation'
import { usePortfolioAccess } from './PortfolioAccessProvider'
import { usePortfolioSession } from './PortfolioSessionProvider'

export default function PortfolioInstrumentRisk({
  portfolioId,
  workspace,
  onAskAssistant,
}: {
  portfolioId: string
  workspace: HoldingsWorkspaceResponse
  onAskAssistant: (instrumentId: string, question: string, reference?: ResearchAssistantReference) => void
}) {
  const zh = useLanguage().language === 'zh-Hans'
  const access = usePortfolioAccess()
  const session = usePortfolioSession()
  const [coverage, setCoverage] = useState<string[] | null>(null)
  const rows = workspace.rows.filter(
    (row) =>
      row.instrument_core &&
      ['private_fund', 'public_fund', 'equity', 'etf', 'index', 'crypto'].includes(
        row.instrument_core.instrument_type,
      ) &&
      row.quantity !== 0,
  )
  const ids = [
    ...new Set(rows.map((row) => row.instrument_core!.instrument_id)),
  ].sort()
  const query = new URLSearchParams({
    instrument_ids: ids.join(','),
  }).toString()
  // The mounted panel retains the request identity, while its query follows current holdings.
  const [request] = useState(
    () =>
      async <T,>(path: string, init?: RequestInit): Promise<T> => {
        const response = await requestInstrumentRisk<T>(path, init)
        if (!init?.method && path.startsWith('/risk?'))
          setCoverage(
            (response as RiskWorkspace).instruments.map((i) => i.instrument_id),
          )
        return response
      },
  )
  const uncovered = coverage ? ids.filter((id) => !coverage.includes(id)) : []
  return (
    <section className="portfolio-section-block" aria-label={zh ? '持仓风险关注' : 'Holding risk watch'}>
      <RiskPanel
        canWrite={Boolean(session?.can_write_team_research)}
        canRun={Boolean(access?.can_read)}
        portfolioId={portfolioId}
        request={request}
        query={query}
        heading={zh ? '持仓风险关注' : 'Holding risk watch'}
        instrumentHref={(id) =>
          buildWatchlistInstrumentDetailUrl(id, { tab: 'investment-research', risk: '1' })
        }
        onAskAssistant={onAskAssistant}
        scopeLabel={zh ? '当前组合持仓' : 'Current portfolio holdings'}
        scopeNote={
          <>
            <span>
              {zh ? `持仓日期 ${workspace.as_of_date} · 关联 ${ids.length} 个标的，风险事项使用最新数据。` : `Holdings as of ${workspace.as_of_date} · ${ids.length} instruments. Risk alerts use the latest available data.`}
            </span>
            {uncovered.length > 0 && (
              <p>
                {zh ? '尚未覆盖：' : 'Not covered: '}
                {uncovered
                  .map(
                    (id) =>
                      rows.find(
                        (row) => row.instrument_core?.instrument_id === id,
                      )?.instrument_core?.instrument_name || id,
                  )
                  .join(zh ? '、' : ', ')}
                {zh ? '。' : '.'}
              </p>
            )}
          </>
        }
        instrumentContext={(id) => {
          const matched = rows.filter(
            (row) => row.instrument_core!.instrument_id === id,
          )
          const value =
            matched.length &&
            matched.every((row) => row.market_value_base != null)
              ? matched.reduce((sum, row) => sum + row.market_value_base!, 0)
              : null
          return (
            <span>
              {zh ? '当前权重（证券市值 / 组合净值）' : 'Current weight (security value / portfolio NAV)'}{' '}
              {value != null && workspace.totals.nav
                ? percent((value / workspace.totals.nav) * 100)
                : (zh ? '不可计算' : 'Unavailable')}{' '}
              · {zh ? '权重不代表风险贡献' : 'Weight is not risk contribution'}
            </span>
          )
        }}
      />
    </section>
  )
}
