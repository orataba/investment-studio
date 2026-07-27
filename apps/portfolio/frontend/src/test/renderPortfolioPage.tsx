import type { ReactElement, ReactNode } from 'react'
import { render } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'

export function renderPortfolioPage(
  element: ReactElement,
  route: string,
  routePath: string,
) {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <Routes>
        <Route path={routePath} element={element} />
      </Routes>
    </MemoryRouter>,
  )
}

export function passthroughWorkspaceLayout({ children }: { children: ReactNode }) {
  return <>{children}</>
}
