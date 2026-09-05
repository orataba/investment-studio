// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { useState } from 'react'

import { LanguageProvider, LanguageSelector, matchesSystemLabel } from '../../../../packages/ui/src/i18n'

beforeEach(() => {
  document.cookie = 'investment_studio_language=en; path=/'
  window.history.replaceState(null, '', '/')
})

afterEach(() => cleanup())

describe('DOM translation context', () => {
  it('finds system fields by either display language', () => {
    expect(matchesSystemLabel('Settlement Cash Account', '结算')).toBe(true)
    expect(matchesSystemLabel('产品形态', 'vehicle')).toBe(true)
    expect(matchesSystemLabel('Return 1M', '收益')).toBe(true)
    expect(matchesSystemLabel('Settlement Cash Account', '价格')).toBe(false)
  })
  it('translates lazy system fields and preserves user content and option values through repeated switches', async () => {
    function Fields() {
      const [expanded, setExpanded] = useState(false)
      return <>
        <button onClick={() => setExpanded(!expanded)}>Review</button>
        <span translate="no">Risk</span>
        <p translate="no">Investment Thesis</p>
        <textarea defaultValue="Keep the original 研究 notes" />
        <span>3M</span>
        <button>3M</button>
        <span>All 公募</span>
        {expanded && <section>
          <h2>Settlement Cash Account</h2>
          <label>Fund vehicle<select defaultValue="场外开放式"><option value="场外开放式">场外开放式</option></select></label>
          <button title="View notes on this date">Add Note</button>
          <button aria-label="Sort 1M Vol: ascending">1M Vol</button>
          <span title="2026-08-04 to 2026-09-03 · 31 calendar days · 22 risk observations. Basis: Market Value">Performance details</span>
          <span>Daily risk basis - aligned observations</span>
        </section>}
      </>
    }
    render(<LanguageProvider><LanguageSelector /><Fields /></LanguageProvider>)
    await waitFor(() => expect(screen.getByText('All Public Funds')).toBeTruthy())
    for (let round = 0; round < 2; round += 1) {
      fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'zh-Hans' } })
      fireEvent.click(screen.getByRole('button', { name: '复盘' }))
      await waitFor(() => expect(screen.getByRole('heading', { name: '结算现金账户' })).toBeTruthy())
      expect(screen.getByRole('button', { name: '添加笔记' }).title).toBe('查看该日期笔记')
      expect(screen.getByRole('button', { name: '1 个月波动率：升序' })).toBeTruthy()
      expect(screen.getByText('日度风险口径：观测数据已对齐')).toBeTruthy()
      expect(screen.getByText('业绩详情').title).not.toMatch(/[A-Za-z]/)
      expect((screen.getByLabelText('产品形态') as HTMLSelectElement).value).toBe('场外开放式')
      expect(screen.getByText('Risk')).toBeTruthy()
      expect(screen.getByText('Investment Thesis')).toBeTruthy()
      expect(screen.getByText('3M', { selector: 'span' })).toBeTruthy()
      expect(screen.getByRole('button', { name: '3 个月' })).toBeTruthy()
      expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('Keep the original 研究 notes')
      fireEvent.change(screen.getByLabelText('语言'), { target: { value: 'en' } })
      await waitFor(() => expect(screen.getByRole('heading', { name: 'Settlement Cash Account' })).toBeTruthy())
      expect(screen.getByRole('option', { name: 'Off-exchange open-ended' })).toBeTruthy()
      fireEvent.click(screen.getByRole('button', { name: 'Review' }))
    }
  })

  it('keeps the current language on workspace links without losing deep-link filters', async () => {
    window.history.replaceState(null, '', '/?lang=zh-Hans')
    render(<LanguageProvider><LanguageSelector /><a data-workspace-link href="http://localhost:5174/portfolios/3/holdings/asset?as_of_date=2026-09-04#activity">Portfolio</a></LanguageProvider>)
    const link = screen.getByRole('link') as HTMLAnchorElement
    await waitFor(() => expect(new URL(link.href).searchParams.get('lang')).toBe('zh-Hans'))
    fireEvent.change(screen.getByLabelText('语言'), { target: { value: 'en' } })
    await waitFor(() => expect(new URL(link.href).searchParams.get('lang')).toBe('en'))
    expect(new URL(link.href).searchParams.get('as_of_date')).toBe('2026-09-04')
    expect(new URL(link.href).hash).toBe('#activity')
    expect(new URL(window.location.href).searchParams.get('lang')).toBe('en')
  })

  it('distinguishes a close-dialog action from a market close label', async () => {
    render(
      <LanguageProvider>
        <LanguageSelector />
        <button type="button">Close</button>
        <span>Close</span>
      </LanguageProvider>,
    )

    fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'zh-Hans' } })

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '关闭' })).toBeTruthy()
      expect(screen.getByText('收盘价')).toBeTruthy()
    })

    fireEvent.change(screen.getByLabelText('语言'), { target: { value: 'en' } })
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Close' })).toBeTruthy()
      expect(screen.getByText('Close', { selector: 'span' })).toBeTruthy()
    })
  })
})
