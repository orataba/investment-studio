import { useEffect, useState } from 'react'
import { useLanguage } from '../../../../../packages/ui/src/i18n'

export function observationAge(observationDate: string | null | undefined, referenceDate: string): number | null {
  if (!observationDate || !/^\d{4}-\d{2}-\d{2}$/.test(observationDate)) return null
  const age = (Date.parse(`${referenceDate}T00:00:00Z`) - Date.parse(`${observationDate}T00:00:00Z`)) / 86400000
  return Number.isFinite(age) && age >= 0 ? age : null
}

export default function FreshnessNote({ reason, observationDate }: { reason: string; observationDate?: string | null }) {
  const { language, t } = useLanguage()
  const [referenceDate, setReferenceDate] = useState(() => new Date().toISOString().slice(0, 10))
  useEffect(() => {
    const nextMidnight = Date.parse(`${referenceDate}T00:00:00Z`) + 86400000
    const timer = window.setTimeout(() => setReferenceDate(new Date().toISOString().slice(0, 10)), Math.max(1, nextMidnight - Date.now()))
    return () => window.clearTimeout(timer)
  }, [referenceDate])
  const age = observationAge(observationDate, referenceDate)
  // Age is a reading-time fact, not the age frozen in a materialized snapshot.
  const currentReason = /Latest observation is \d+ calendar days old;/.test(reason) && age != null
    ? reason.replace(/Latest observation is \d+ calendar days old;/, `Latest observation is ${age} calendar days old;`)
    : reason
  return <>{language === 'zh-Hans' && age != null
    ? `最新观察日 ${observationDate}；截至 ${referenceDate} UTC 已过去 ${age} 个自然日。${/calendar days old;/.test(reason) ? '数据更新滞后，已超过允许天数。' : t(currentReason)}`
    : <>{t(currentReason)}{age != null && ` Observation: ${observationDate}; as of ${referenceDate} UTC (${age} calendar days).`}</>}</>
}
