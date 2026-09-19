import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { LanguageProvider } from '../../../packages/ui/src/i18n'
import StudioHome, { StudioLinks } from './StudioHome'
import appCatalog from '../../apps.json'
import LoginPage, { destinationAfterLogin } from './LoginPage'
import { resolveWorkspaceUrl } from '../../../packages/ui/src/navigation'
import ActivatePage from './ActivatePage'
import AccountPage from './AccountPage'
import { homeMessages } from './messages'
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
  it('renders the four workspace entrances on Home', () => {
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
    expect(markup).toContain('Market Briefing')
    expect(markup).toContain('href="http://127.0.0.1:5175"')
    expect(markup).not.toContain('Email NAV')
    expect(markup).not.toContain('operational status')
    expect(markup).not.toContain('/api/dashboard')
  })

  it.each([
    { language: 'zh-Hans', name: '市场简报', eyebrow: '日报与周报', description: '阅读市场日报与周报、查阅来源依据、跟踪预期变化。', other: 'Market Briefing' },
    { language: 'en', name: 'Market Briefing', eyebrow: 'Daily and weekly research', description: 'Read market briefings, source evidence, and changes in expectations.', other: '市场简报' },
  ])('renders the complete Briefing entry in $language with the public workspace URL', ({ language, name, eyebrow, description, other }) => {
    vi.stubGlobal('window', { location: { hostname: 'yunguyungu.com', protocol: 'https:', port: '', search: `?lang=${language}` } })
    const markup = renderToStaticMarkup(<LanguageProvider><StudioLinks apps={appCatalog} /></LanguageProvider>)
    expect(markup).toContain(name)
    expect(markup).toContain(eyebrow)
    expect(markup).toContain(description)
    expect(markup).not.toContain(other)
    expect(markup).toContain('href="https://briefing.yunguyungu.com"')
  })

  it('renders an added app and removes entries absent from the catalog', () => {
    const markup = renderToStaticMarkup(<LanguageProvider><StudioLinks apps={[{
      app_id: 'research', name: 'Research', url: 'https://research.example.test',
      eyebrow: 'Research', description: 'Independent research app',
    }]} /></LanguageProvider>)
    expect(markup).toContain('https://research.example.test')
    expect(markup).not.toContain('Watchlist')
    expect(renderToStaticMarkup(<LanguageProvider><StudioLinks apps={[]} /></LanguageProvider>)).not.toContain('<a ')
  })

  it('shows a loading message before the entry list is available', () => {
    expect(renderToStaticMarkup(<LanguageProvider><StudioHome /></LanguageProvider>))
      .toContain('aria-busy="true"')
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

  it.each([
    { language: 'en', title: 'Set your account password', accountTitle: 'Account and team' },
    { language: 'zh-Hans', title: '设置账号密码', accountTitle: '账号与团队' },
  ])('renders identity pages in $language and waits for the account before showing signed-out UI', ({ language, title, accountTitle }) => {
    vi.stubGlobal('window', { location: { hostname: '127.0.0.1', protocol: 'http:', search: `?lang=${language}`, hash: '#token=example' } })
    const page = (child: React.ReactNode) => renderToStaticMarkup(<LanguageProvider messages={homeMessages}>{child}</LanguageProvider>)
    const activate = page(<ActivatePage />)
    expect(activate).toContain(title)
    expect(activate).toContain('class="language-switcher"')
    expect(page(<LoginPage />)).not.toContain('name="otp"')
    const account = page(<AccountPage />)
    expect(account).toContain(accountTitle)
    expect(account).toContain('aria-busy="true"')
    expect(account).not.toContain('href="/login"')
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
