import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { NavImportButton } from './NavImportButton'

describe('NavImportButton', () => {
  it('renders the NAV action for funds', () => {
    const markup = renderToStaticMarkup(
      <NavImportButton instrumentType="fund" type="button" className="nav-action" />,
    )

    expect(markup).toContain('Import NAV')
    expect(markup).toContain('class="nav-action"')
  })

  it.each(['equity', 'etf', 'fx', 'bond'] as const)(
    'hides the NAV action for %s instruments',
    (instrumentType) => {
      expect(renderToStaticMarkup(<NavImportButton instrumentType={instrumentType} />)).toBe('')
    },
  )
})
