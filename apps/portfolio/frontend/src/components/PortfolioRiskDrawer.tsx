import { useEffect, useState } from 'react'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'
import { WorkspaceToolIcon } from '../../../../../packages/ui/src/WorkspaceTools'
import { getHoldingsWorkspace, type HoldingsWorkspaceResponse } from '../lib/api'
import PortfolioInstrumentRisk from './PortfolioInstrumentRisk'
import './portfolio-risk-drawer.css'

export default function PortfolioRiskDrawer({ portfolioId, onClose, onAskAssistant }: {
  portfolioId: string
  onClose: () => void
  onAskAssistant: (instrumentId: string, question: string) => void
}) {
  const { language } = useLanguage()
  const zh = language === 'zh-Hans'
  const dialogRef = useModalDialog(true, onClose)
  const [workspace, setWorkspace] = useState<HoldingsWorkspaceResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    getHoldingsWorkspace(portfolioId, { include_details: true }).then(
      (value) => { if (!cancelled) setWorkspace(value) },
      (reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason)) },
    )
    return () => { cancelled = true }
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
        {error ? <p role="alert">{error}</p> : workspace
          ? <PortfolioInstrumentRisk portfolioId={portfolioId} workspace={workspace} onAskAssistant={onAskAssistant} />
          : <p role="status">{zh ? '正在整理组合风险…' : 'Loading portfolio risks…'}</p>}
      </div>
    </div>
  </div>
}
