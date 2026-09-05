import React from 'react'
import ReactDOM from 'react-dom/client'
import { LanguageProvider } from '../../../packages/ui/src/i18n'
import InputModality from '../../../packages/ui/src/InputModality'
import App from './App'
import './index.css'
import '../../../packages/ui/src/language.css'
import '../../../packages/ui/src/studio-theme.css'
import './app-theme.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <LanguageProvider>
      <InputModality />
      <App />
    </LanguageProvider>
  </React.StrictMode>,
)
