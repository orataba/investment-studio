import { installBrowserDiagnostics } from '../../../../packages/ui/src/diagnostics'
import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import { LanguageProvider } from '../../../../packages/ui/src/i18n'
import InputModality from '../../../../packages/ui/src/InputModality'
import App from './App'
import './index.css'
import '../../../../packages/ui/src/language.css'
import '../../../../packages/ui/src/notice-toast.css'
import '../../../../packages/ui/src/sparkline.css'
import '../../../../packages/ui/src/studio-theme.css'
import './app-theme.css'

const routerBasename = import.meta.env.BASE_URL === '/'
  ? undefined
  : import.meta.env.BASE_URL.replace(/\/$/, '')

installBrowserDiagnostics('portfolio', import.meta.env.VITE_HOME_URL)

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <LanguageProvider>
      <InputModality />
      <BrowserRouter basename={routerBasename}>
        <App />
      </BrowserRouter>
    </LanguageProvider>
  </React.StrictMode>,
)
