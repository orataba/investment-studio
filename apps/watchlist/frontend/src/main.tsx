import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import { LanguageProvider } from '../../../../packages/ui/src/i18n'

import App from './App'
import './index.css'
import '../../../../packages/ui/src/language.css'
import '../../../../packages/ui/src/notice-toast.css'
import '../../../../packages/ui/src/sparkline.css'

const routerBasename = import.meta.env.BASE_URL === '/'
  ? undefined
  : import.meta.env.BASE_URL.replace(/\/$/, '')

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <LanguageProvider>
      <BrowserRouter basename={routerBasename}>
        <App />
      </BrowserRouter>
    </LanguageProvider>
  </React.StrictMode>
)
