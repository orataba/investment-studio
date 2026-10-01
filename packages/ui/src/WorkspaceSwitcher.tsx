import { useLanguage } from './i18n'
import { resolveWorkspaceUrl, withLanguage } from './navigation'
import './workspace-switcher.css'

const workspaces = [
  ['watchlist', 'Watchlist'], ['portfolio', 'Portfolio'],
  ['regime', 'Regime'], ['briefing', 'Market Briefing'],
] as const
const configuredUrls = {
  watchlist: import.meta.env.VITE_WATCHLIST_URL,
  portfolio: import.meta.env.VITE_PORTFOLIO_URL,
  regime: import.meta.env.VITE_REGIME_URL,
  briefing: import.meta.env.VITE_BRIEFING_URL,
}

export default function WorkspaceSwitcher({ current }: { current: typeof workspaces[number][0] }) {
  const { language, t } = useLanguage()
  return <select className="studio-workspace-switcher" aria-label={language === 'zh-Hans' ? '切换工作区' : 'Switch workspace'} value={current}
    onChange={event => {
      const workspace = workspaces.find(([key]) => key === event.target.value)?.[0]
      if (workspace && workspace !== current) window.location.assign(withLanguage(resolveWorkspaceUrl(configuredUrls[workspace], workspace), language))
    }}>
    {workspaces.map(([key, label]) => <option key={key} value={key}>{t(label)}</option>)}
  </select>
}
