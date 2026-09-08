import StudioHome from './StudioHome'
import LoginPage from './LoginPage'
import AccountPage from './AccountPage'
import ActivatePage from './ActivatePage'
import { appPath, stripAppBasePath } from './appPath'

export default function App() {
  const path = stripAppBasePath(window.location.pathname).replace(/\/+$/, '') || '/'
  if (path === '/account') return <AccountPage />
  if (path === '/activate') return <ActivatePage />
  if (path === '/login') return <LoginPage />
  if (path === '/') return <StudioHome />
  return <main className="studio-shell"><h1>Page not found</h1><a href={appPath('/')}>Investment Studio</a></main>
}
