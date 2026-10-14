import CorrelationObservation, { type CorrelationRiskObservation } from './CorrelationObservation'
import RiskOfficerPanel from '../../../../../packages/ui/src/RiskOfficerPanel'
import { usePortfolioAccess } from './PortfolioAccessProvider'
import { usePortfolioSession } from './PortfolioSessionProvider'
import WorkspaceSkeleton from '../../../../../packages/ui/src/WorkspaceSkeleton'
import { useEffect, useState } from 'react'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'
import { WorkspaceToolIcon } from '../../../../../packages/ui/src/WorkspaceTools'
import { getHoldingsWorkspace, requestInstrumentRisk, type HoldingsWorkspaceResponse } from '../lib/api'
import PortfolioInstrumentRisk from './PortfolioInstrumentRisk'
import type { ResearchAssistantReference } from '../../../../../packages/ui/src/researchReference'
import './portfolio-risk-drawer.css'

export default function PortfolioRiskDrawer({ portfolioId, onClose, onAskAssistant, correlationObservation }: {
  portfolioId: string
  correlationObservation?: CorrelationRiskObservation
  onClose: () => void
  onAskAssistant: (instrumentId: string, question: string, reference?: ResearchAssistantReference) => void
}) {
  const { language } = useLanguage()
  const access = usePortfolioAccess()
  const session = usePortfolioSession()
  const [reviewRevision, setReviewRevision] = useState(0)
  const zh = language === 'zh-Hans'
  const dialogRef = useModalDialog(true, onClose)
  const [workspace, setWorkspace] = useState<HoldingsWorkspaceResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const controller = new AbortController()
    setWorkspace(null)
    setError(null)
    getHoldingsWorkspace(portfolioId, { include_details: true }, controller.signal).then(
      (value) => { if (!cancelled) setWorkspace(value) },
      (reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason)) },
    )
    return () => { cancelled = true; controller.abort() }
  }, [portfolioId])

  return <div className="portfolio-risk-backdrop" onClick={(event) => {
    if (event.target === event.currentTarget) onClose()
  }}>
    <div ref={dialogRef} className="portfolio-risk-drawer" role="dialog" aria-modal="true" aria-labelledby="portfolio-risk-title" tabIndex={-1}>
      <header className="portfolio-risk-heading">
        <h1 id="portfolio-risk-title"><WorkspaceToolIcon kind="risk" />{zh ? '风险提示' : 'Risk alerts'}</h1>
        <button type="button" onClick={onClose} aria-label={zh ? '关闭风险提示' : 'Close risk alerts'}>{zh ? '关闭' : 'Close'}</button>
      </header>
      <div className="portfolio-risk-body">
        {correlationObservation ? <CorrelationObservation {...correlationObservation} alertOnly /> : null}
        <RiskOfficerPanel request={requestInstrumentRisk} scopeQuery={new URLSearchParams({ portfolio_id: portfolioId }).toString()} canRun={Boolean(access?.can_read)} canWrite={Boolean(session?.can_write_team_research)} canCorrect={Boolean(access?.can_edit)} onCompleted={() => setReviewRevision(value => value + 1)} />
        {error ? <p role="alert">{error}</p> : workspace
          ? <PortfolioInstrumentRisk key={`${portfolioId}:${reviewRevision}`} showOfficer={false} portfolioId={portfolioId} workspace={workspace} onAskAssistant={onAskAssistant} />
          : <WorkspaceSkeleton />}
      </div>
    </div>
  </div>
}
