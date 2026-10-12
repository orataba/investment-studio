import type { ReactNode } from 'react'
import { useSearchParams } from 'react-router'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import { resolveWorkspaceUrl, withLanguage } from '../../../../../packages/ui/src/navigation'

/** One saved research document with a focused reading presentation; no second
 * research API, generated summary, or independent copy of the report. */
export default function ResearchPresentation({ children }: { children: ReactNode }) {
  const [params, setParams] = useSearchParams()
  const { language } = useLanguage()
  const reading = params.get('mode') === 'report' && params.get('tab') === 'investment-research'
  const zh = language === 'zh-Hans'
  return <div className={reading ? 'research-focused-reader' : undefined}>
    {reading && <nav className="research-reader-toolbar" aria-label={zh ? '报告阅读' : 'Report reading'}>
      {params.get('from') === 'briefing' ? <a data-workspace-link href={withLanguage(`${resolveWorkspaceUrl(undefined, 'briefing')}/?type=industry`, language)}>{zh ? '返回市场简报 · 行业研究' : 'Back to Briefing · Industry research'}</a> : <span>{zh ? '研究报告' : 'Research report'}</span>}
      <button type="button" onClick={() => setParams(current => { const next = new URLSearchParams(current); next.delete('mode'); next.delete('from'); return next })}>{zh ? '返回完整工作区' : 'Open full workspace'}</button>
    </nav>}
    {children}
  </div>
}
