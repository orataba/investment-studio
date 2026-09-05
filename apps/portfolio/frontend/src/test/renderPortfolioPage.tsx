import type { ReactElement, ReactNode } from 'react'
import { render } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { LanguageProvider } from '../../../../../packages/ui/src/i18n'

export function renderPortfolioPage(
  element: ReactElement,
  route: string,
  routePath: string,
) {
  return render(
    <LanguageProvider enableDomTranslation={false}><MemoryRouter initialEntries={[route]}>
      <Routes>
        <Route path={routePath} element={element} />
      </Routes>
    </MemoryRouter></LanguageProvider>,
  )
}

export function passthroughWorkspaceLayout({ children }: { children: ReactNode }) {
  return <>{children}</>
}
