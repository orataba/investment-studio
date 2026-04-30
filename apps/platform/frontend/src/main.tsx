import React from 'react'
import ReactDOM from 'react-dom/client'
import { LanguageProvider } from '../../../../packages/ui/src/i18n'
import App from './App'
import './index.css'
import '../../../../packages/ui/src/language.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <LanguageProvider>
      <App />
    </LanguageProvider>
  </React.StrictMode>,
)
