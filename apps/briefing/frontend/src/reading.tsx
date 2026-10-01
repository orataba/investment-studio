export function EvidenceTime({ value, timezone = 'Asia/Shanghai' }: { value?: string; timezone?: string }) {
  if (!value) return <>—</>
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return <time dateTime={value}>{value}</time>
  if (!/(Z|[+-]\d{2}:?\d{2})$/.test(value)) return <span>{value}</span>
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return <span>{value}</span>
  const parts = new Intl.DateTimeFormat('en-GB', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(date)
  const part = (type: string) => parts.find(item => item.type === type)?.value
  return <time dateTime={value} title={value}>{part('year')}-{part('month')}-{part('day')} {part('hour')}:{part('minute')}</time>
}

export function navigateToSection(id: string, saveHistory = true) {
  const section = document.getElementById(id)
  if (!section) return
  if (saveHistory && window.location.hash !== `#${id}`) {
    window.history.replaceState({ ...window.history.state, readingScrollY: window.scrollY, readingFocusId: document.activeElement?.id }, '', window.location.href)
    const url = new URL(window.location.href)
    url.hash = id
    window.history.pushState(null, '', url)
  }
  if (section instanceof HTMLDetailsElement) section.open = true
  const target = section.querySelector<HTMLElement>('summary, h2') || section
  target.setAttribute('tabindex', '-1')
  target.focus({ preventScroll: true })
  section.scrollIntoView?.({ block: 'start' })
}

export function ExternalLinkIcon() {
  return <svg className="external-link-icon" aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4"><path d="M9 2h5v5M14 2 7 9M6 3H3v10h10v-3" /></svg>
}
