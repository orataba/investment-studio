import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { LanguageProvider } from '../../../packages/ui/src/i18n'
import StudioHome, { StudioLinks } from './StudioHome'
import appCatalog from '../../apps.json'
import LoginPage, { destinationAfterLogin } from './LoginPage'
import { resolveWorkspaceUrl } from '../../../packages/ui/src/navigation'
afterEach(() => {
  vi.unstubAllGlobals()
})

describe('StudioHome', () => {
  it('resolves public workspace hosts and preserves configured local ports and base paths', () => {
    vi.stubGlobal('window', { location: { hostname: 'regime.yunguyungu.com', protocol: 'https:', port: '' } })
    expect(resolveWorkspaceUrl(undefined, 'home')).toBe('https://yunguyungu.com')
    expect(resolveWorkspaceUrl('http://127.0.0.1:3011', 'regime')).toBe('https://regime.yunguyungu.com')
    vi.stubGlobal('window', { location: { hostname: '192.168.1.20', protocol: 'http:', port: '5172' } })
    expect(resolveWorkspaceUrl('http://127.0.0.1:3011', 'regime')).toBe('http://192.168.1.20:3011')
    expect(resolveWorkspaceUrl('/studio/', 'home')).toBe('/studio')
  })
  it('renders the three workspace entrances on Home', () => {
    vi.stubGlobal('window', {
      location: {
        hostname: '127.0.0.1',
        protocol: 'http:',
        search: '',
      },
    })

    const markup = renderToStaticMarkup(
      <LanguageProvider>
        <StudioLinks apps={appCatalog} />
      </LanguageProvider>,
    )

    expect(markup).toContain('href="http://127.0.0.1:5173"')
    expect(markup).toContain('href="http://127.0.0.1:5174"')
    expect(markup).not.toContain('href="/instruments"')
    expect(markup).toContain('href="http://127.0.0.1:3011"')
    expect(markup).toContain('Watchlist')
    expect(markup).toContain('Portfolio')
    expect(markup).toContain('Regime')
    expect(markup).not.toContain('Email NAV')
    expect(markup).not.toContain('operational status')
    expect(markup).not.toContain('/api/dashboard')
  })

  it('renders an added app and removes entries absent from the catalog', () => {
    const markup = renderToStaticMarkup(<StudioLinks apps={[{
      app_id: 'research', name: 'Research', url: 'https://research.example.test',
      eyebrow: 'Research', description: 'Independent research app',
    }]} />)
    expect(markup).toContain('https://research.example.test')
    expect(markup).not.toContain('Watchlist')
    expect(renderToStaticMarkup(<StudioLinks apps={[]} />)).not.toContain('<a ')
  })

  it('shows a loading message before the entry list is available', () => {
    expect(renderToStaticMarkup(<LanguageProvider><StudioHome /></LanguageProvider>))
      .toContain('Loading workspaces')
  })

  it('renders a full login form', () => {
    vi.stubGlobal('window', {
      location: {
        hostname: 'yunguyungu.com',
        origin: 'https://yunguyungu.com',
        protocol: 'https:',
        search: '',
      },
    })

    const markup = renderToStaticMarkup(<LanguageProvider><LoginPage /></LanguageProvider>)

    expect(markup).toContain('name="username"')
    expect(markup).toContain('name="password"')
    expect(markup).toContain('Enter Investment Studio')
  })

  it('preserves the complete authenticated deep link including every query filter', () => {
    vi.stubGlobal('window', {
      location: {
        hostname: 'yunguyungu.com',
        origin: 'https://yunguyungu.com',
        protocol: 'https:',
        search: (
          '?next=https://portfolio.yunguyungu.com/portfolios/3/holdings/security-1'
          + '?as_of_date=2026-09-04&holding_line_id=line-2&detail_tab=transactions'
        ),
      },
    })

    expect(destinationAfterLogin()).toBe(
      'https://portfolio.yunguyungu.com/portfolios/3/holdings/security-1'
      + '?as_of_date=2026-09-04&holding_line_id=line-2&detail_tab=transactions',
    )
  })

  it('rejects a deep-link destination outside the authenticated host family', () => {
    vi.stubGlobal('window', {
      location: {
        hostname: 'yunguyungu.com',
        origin: 'https://yunguyungu.com',
        protocol: 'https:',
        search: '?next=https://example.com/steal-session?from=portfolio',
      },
    })

    expect(destinationAfterLogin()).toBe('/')
  })
})
