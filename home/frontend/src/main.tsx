import React from 'react'
import ReactDOM from 'react-dom/client'
import { LanguageProvider } from '../../../packages/ui/src/i18n'
import InputModality from '../../../packages/ui/src/InputModality'
import App from './App'
import { homeMessages } from './messages'
import './index.css'
import '../../../packages/ui/src/language.css'
import '../../../packages/ui/src/studio-theme.css'
import './app-theme.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <LanguageProvider messages={homeMessages}>
      <InputModality />
      <App />
    </LanguageProvider>
  </React.StrictMode>,
)
