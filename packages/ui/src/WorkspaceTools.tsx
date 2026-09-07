import { useLanguage } from './i18n'
import './workspace-tools.css'

type Action = { onClick?: () => void; href?: string; disabled?: boolean; label?: string; count?: number }
type ToolKind = 'settings' | 'risk' | 'assistant'

export function WorkspaceToolIcon({ kind }: { kind: ToolKind }) {
  return <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {kind === 'settings' ? <>
      <path d="m9 3-.6 2.3-2 .9-2.2-.6-2 3.5 1.6 1.7v2.4l-1.6 1.7 2 3.5 2.2-.6 2 .9.6 2.3h4l.6-2.3 2-.9 2.2.6 2-3.5-1.6-1.7v-2.4l1.6-1.7-2-3.5-2.2.6-2-.9L13 3Z" />
      <circle cx="11" cy="12" r="3" />
    </> : kind === 'risk' ? <>
      <path d="M12 3 4.5 6v5c0 4.4 3 7.8 7.5 10 4.5-2.2 7.5-5.6 7.5-10V6Z" />
      <path d="M12 8v5m0 3h.01" />
    </> : <>
      <path d="M19.5 13.5V16a2 2 0 0 1-2 2H9l-5 3V6a2 2 0 0 1 2-2h7" />
      <path d="m18 2 1.4 3.6L23 7l-3.6 1.4L18 12l-1.4-3.6L13 7l3.6-1.4ZM8 11h3m-3 3h7" />
    </>}
  </svg>
}

export default function WorkspaceTools({ settings, risk, assistant }: { settings: Action; risk: Action; assistant: Action }) {
  const { language } = useLanguage()
  const zh = language === 'zh-Hans'
  const labels = { settings: zh ? '设置' : 'Settings', risk: zh ? '风险提示' : 'Risk alerts', assistant: zh ? '研究助手' : 'Research assistant' }
  const actions = { settings, risk, assistant }
  return <div className="workspace-context-tools" role="group" aria-label={zh ? '当前对象工具' : 'Current workspace tools'}>
    {(['settings', 'risk', 'assistant'] as const).map((kind) => {
      const action = actions[kind]
      const label = action.label || labels[kind]
      const content = <><WorkspaceToolIcon kind={kind} /><span>{labels[kind]}</span>{Boolean(action.count) && <span aria-hidden="true">({action.count})</span>}</>
      return action.href && !action.disabled
        ? <a key={kind} href={action.href} data-workspace-link aria-label={label} title={label} onClick={action.onClick}>{content}</a>
        : <button key={kind} type="button" aria-label={label} title={label} onClick={action.onClick} disabled={action.disabled}>{content}</button>
    })}
  </div>
}
