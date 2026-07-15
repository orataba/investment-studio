import type { ReactElement, ReactNode } from 'react'
import { render } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

export function renderPortfolioPage(
  element: ReactElement,
  route: string,
  routePath: string,
) {
  return render(
    <MemoryRouter
      initialEntries={[route]}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route path={routePath} element={element} />
      </Routes>
    </MemoryRouter>,
  )
}

export function passthroughWorkspaceLayout({ children }: { children: ReactNode }) {
  return <>{children}</>
}
