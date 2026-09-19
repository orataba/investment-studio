import { installBrowserDiagnostics } from '../../../../packages/ui/src/diagnostics'
import React from 'react'
import ReactDOM from 'react-dom/client'
import { LanguageProvider } from '../../../../packages/ui/src/i18n'
import InputModality from '../../../../packages/ui/src/InputModality'
import '../../../../packages/ui/src/studio-theme.css'
import '../../../../packages/ui/src/language.css'
import App from './App'
import './style.css'

installBrowserDiagnostics('briefing', import.meta.env.VITE_HOME_URL)

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><LanguageProvider enableDomTranslation={false}><InputModality /><App /></LanguageProvider></React.StrictMode>,
)
