import { useLanguage } from '../../../../packages/ui/src/i18n'
import { EvidenceTime } from './reading'
import type { BriefingSchedule, EditionAvailability } from './types'

export default function EditionStatus({ schedule, editions, timezone, onRead }: {
  schedule?: BriefingSchedule; editions?: EditionAvailability; timezone?: string; onRead: (id: string) => void
}) {
  const { language } = useLanguage()
  const copy = (zh: string, en: string) => language === 'zh-Hans' ? zh : en
  if (!schedule && !editions) return null
  const latest = editions?.latest
  const completed = editions?.latest_completed
  const state = schedule?.state || 'unknown'
  const label = {
    scheduled: copy('下一次定时生成', 'Next scheduled generation'),
    disabled: copy('定时生成未启用', 'Scheduled generation is disabled'),
    stopped: copy('定时生成已停止', 'Scheduled generation is stopped'),
    unknown: copy('暂时无法确认生成计划', 'The generation schedule is unavailable'),
    not_managed: copy('本地预览，不自动发布', 'Local preview; no automatic publication'),
  }[state]
  const attempt = latest && latest.status !== 'completed' ? {
    queued: copy('等待生成', 'Queued'), running: copy('正在生成', 'Generating'),
    failed: copy('本次未完成', 'This attempt did not complete'), paused: copy('本次已暂停', 'This attempt is paused'),
  }[latest.status] : null
  return <section className="edition-availability" aria-label={copy('简报更新状态', 'Edition availability')}>
    <span><strong>{label}</strong>{state === 'scheduled' && schedule?.next_run_at
      ? <> · <EvidenceTime value={schedule.next_run_at} timezone={timezone} /></>
      : (state === 'disabled' || state === 'stopped') && copy(' · 没有下一计划时间', ' · No next run is scheduled')}</span>
    {attempt && latest && <span role="status">{latest.report_date} · {attempt}</span>}
    {completed ? <button onClick={() => onRead(completed.report_id)}>{copy('最近可读版本', 'Latest readable edition')} · {completed.report_date}</button>
      : editions && <span>{copy('当前没有可读版本', 'No readable edition is available')}</span>}
  </section>
}
