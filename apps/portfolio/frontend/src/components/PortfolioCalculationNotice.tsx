import { useLanguage } from '../../../../../packages/ui/src/i18n'
import { getPortfolioCalculationStatus } from '../lib/api'
import { useEffect, useState } from 'react'
import NoticeToast, { LoadingNotice, type NoticeToastMessage } from '../../../../../packages/ui/src/NoticeToast'

/** Observe the durable worker state without requesting financial calculations. */
export default function PortfolioCalculationNotice({ portfolioId, revision, fallbackError }: { portfolioId: string; revision: unknown; fallbackError?: string }) {
  const zh = useLanguage().language.startsWith('zh')
  const [notice, setNotice] = useState<NoticeToastMessage | null>(null)
  const [loading, setLoading] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const [lastPublishedDate, setLastPublishedDate] = useState<string | null>(null)
  useEffect(() => {
    setNotice(null)
    setFailure(null)
    setLastPublishedDate(null)
    setLoading(false)
    if (!portfolioId) return
    let active = true
    let pending = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const controller = new AbortController()
    async function check() {
      try {
        const state = await getPortfolioCalculationStatus(portfolioId, controller.signal)
        if (!active) return
        setLastPublishedDate(state.refreshed_to)
        setLoading(state.status === 'stale' || state.status === 'running')
        if (state.status === 'stale' || state.status === 'running') {
          pending = true
          setNotice(null)
          timer = setTimeout(() => { void check() }, 2000)
        } else if (state.status === 'failed') {
          setFailure(zh ? `净值更新未完成：${state.error_message || '请检查行情与核算状态。'}` : `Valuation update incomplete: ${state.error_message || 'Check market data and calculation status.'}`)
        } else if (pending && state.status === 'current') {
          setNotice({ id: Date.now(), tone: 'success', message: zh ? `组合净值与持仓已更新${state.refreshed_to ? `至 ${state.refreshed_to}` : ''}。` : `Portfolio valuation and holdings updated${state.refreshed_to ? ` through ${state.refreshed_to}` : ''}.` })
        } else {
          setNotice(null)
        }
      } catch (error) {
        if (active && !controller.signal.aborted) {
          setLoading(false)
          if (pending) setNotice({ id: Date.now(), tone: 'error', message: error instanceof Error ? error.message : zh ? '无法读取更新状态。' : 'Unable to read update status.' })
        }
      }
    }
    void check()
    return () => { active = false; controller.abort(); if (timer) clearTimeout(timer) }
  }, [portfolioId, revision, zh])
  return <>{failure || fallbackError ? <div className="inline-notice inline-notice-warning" role="alert"><p>{failure || fallbackError}</p><p>{lastPublishedDate ? `${zh ? '最近已发布估值' : 'Last published valuation'}: ${lastPublishedDate}` : (zh ? '未取得已发布估值日期。' : 'Published valuation date unavailable.')}</p>{lastPublishedDate ? <a href={`${import.meta.env.BASE_URL}portfolios/${encodeURIComponent(portfolioId)}/performance?end_date=${lastPublishedDate}`}>{zh ? '查看该估值日期' : 'Review this valuation date'}</a> : null}</div> : null}<LoadingNotice active={loading} message={zh ? '组合净值与持仓更新中…' : 'Updating portfolio valuation and holdings…'} compact /><NoticeToast notice={notice} onDismiss={() => setNotice(null)} /></>
}
