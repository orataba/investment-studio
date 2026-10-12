// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { MemoryRouter, useLocation } from 'react-router'
import { LanguageProvider } from '../../../../../packages/ui/src/i18n'
import ResearchPresentation from './ResearchPresentation'

afterEach(cleanup)
function Location() { return <output data-testid="location">{useLocation().search}</output> }
it('reads the same saved content and exits without dropping research or navigation context', () => {
  window.history.replaceState(null, '', '/?lang=en')
  render(<LanguageProvider enableDomTranslation={false}><MemoryRouter initialEntries={['/instruments/xlk?tab=investment-research&mode=report&from=briefing&watchlist=sectors']}><ResearchPresentation><p>Saved research with evidence</p><Location /></ResearchPresentation></MemoryRouter></LanguageProvider>)
  expect(screen.getByText('Saved research with evidence')).toBeTruthy()
  expect(screen.getByRole('navigation', { name: 'Report reading' })).toBeTruthy()
  expect(screen.getByRole('link', { name: 'Back to Briefing · Industry research' }).getAttribute('href')).toContain('?type=industry')
  fireEvent.click(screen.getByRole('button', { name: 'Open full workspace' }))
  expect(screen.queryByRole('navigation', { name: 'Report reading' })).toBeNull()
  expect(screen.getByTestId('location').textContent).toBe('?tab=investment-research&watchlist=sectors')
  expect(screen.getByText('Saved research with evidence')).toBeTruthy()
})
