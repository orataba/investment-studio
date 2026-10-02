import { afterEach, describe, expect, it, vi } from 'vitest'
import { destinationAfterLogin, normalizeProxyLoginDestination } from './loginDestination'
import { withLanguage } from '../../../packages/ui/src/navigation'

afterEach(() => vi.unstubAllGlobals())
function location(search: string, hash = '') {
  const replaceState = vi.fn()
  const browser = { location: { pathname: '/login', href: 'https://yunguyungu.com/login', search, hash, origin: 'https://yunguyungu.com', hostname: 'yunguyungu.com', protocol: 'https:' }, history: { state: null, replaceState } }
  vi.stubGlobal('window', browser)
  return browser
}
describe('encoded login destination contract', () => {
  it.each([
    'https://watchlist.yunguyungu.com/watchlists/example',
    'https://portfolio.yunguyungu.com/portfolios/p1?date=2026-10-01&account=a%26b&label=%E4%B8%AD%E6%96%87&value=50%25#holdings',
    'https://watchlist.yunguyungu.com/instruments/中文?query=100%25&tab=research',
  ])('roundtrips a client next link with an independent language: %s', target => {
    location('')
    const url = withLanguage(`/login?next=${encodeURIComponent(target)}`, 'zh-Hans')
    location(url.slice(url.indexOf('?')))
    expect(destinationAfterLogin()).toBe(new URL(target).href)
  })
  it('normalizes the proxy handoff exactly once without decoding URI escapes', () => {
    const target = 'https://portfolio.yunguyungu.com/p1?account=a%26b&title=%E4%B8%AD&percent=50%25&lang=zh-Hans'
    const browser = location('', `#next=${target}`)
    normalizeProxyLoginDestination()
    const normalized = browser.history.replaceState.mock.calls[0][2] as string
    expect(new URLSearchParams(normalized.split('?')[1]).get('next')).toBe(target)
    browser.location.search = normalized.slice(normalized.indexOf('?'))
    browser.location.hash = ''
    normalizeProxyLoginDestination()
    expect(browser.history.replaceState).toHaveBeenCalledTimes(1)
    expect(destinationAfterLogin()).toBe(target)
  })
  it.each(['https://example.com/p1', 'https://yunguyungu.com.attacker.test/p1', 'http://portfolio.yunguyungu.com/p1', 'javascript:alert(1)'])('rejects an untrusted target: %s', target => {
    location(`?next=${encodeURIComponent(target)}&lang=en`)
    expect(destinationAfterLogin()).toBe('/')
  })
})
