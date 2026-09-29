import { getPortfolioCalculationStatus } from '../lib/api'
import { useEffect, useState } from 'react'
import NoticeToast, { type NoticeToastMessage } from '../../../../../packages/ui/src/NoticeToast'

/** Observe the durable worker state without requesting financial calculations. */
export default function PortfolioCalculationNotice({ portfolioId, revision }: { portfolioId: string; revision: unknown }) {
  const [notice, setNotice] = useState<NoticeToastMessage | null>(null)
  useEffect(() => {
    setNotice(null)
    if (!portfolioId) return
    let active = true
    let pending = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const controller = new AbortController()
    async function check() {
      try {
        const state = await getPortfolioCalculationStatus(portfolioId, controller.signal)
        if (!active) return
        if (state.status === 'stale' || state.status === 'running') {
          pending = true
          setNotice({ id: 0, tone: 'loading', message: '组合净值与持仓更新中…' })
          timer = setTimeout(() => { void check() }, 2000)
        } else if (state.status === 'failed') {
          setNotice({ id: Date.now(), tone: 'error', message: `净值更新未完成：${state.error_message || '请检查行情与核算状态。'}` })
        } else if (pending && state.status === 'current') {
          setNotice({ id: Date.now(), tone: 'success', message: `组合净值与持仓已更新${state.refreshed_to ? `至 ${state.refreshed_to}` : ''}。` })
        } else {
          setNotice(null)
        }
      } catch (error) {
        if (active && !controller.signal.aborted && pending) setNotice({ id: Date.now(), tone: 'error', message: error instanceof Error ? error.message : '无法读取更新状态。' })
      }
    }
    void check()
    return () => { active = false; controller.abort(); if (timer) clearTimeout(timer) }
  }, [portfolioId, revision])
  return <NoticeToast notice={notice} onDismiss={() => setNotice(null)} />
}
