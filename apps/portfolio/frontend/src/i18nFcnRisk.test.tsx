// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import {
  LANGUAGE_STORAGE_KEY,
  LanguageProvider,
  LanguageSelector,
} from '../../../../packages/ui/src/i18n'
import { transactionActionGroups } from './lib/transactionActions'

beforeEach(() => {
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, 'en')
})

afterEach(() => cleanup())

it('localizes the pre-inception holdings boundary without changing the date', async () => {
  const error = 'Portfolio has no holdings before its inception date (2026-07-01). Choose the inception date or a later date.'
  render(<LanguageProvider><LanguageSelector /><div role="alert">{error}</div></LanguageProvider>)
  fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'zh-Hans' } })
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('组合在成立日（2026-07-01）之前尚无持仓，请选择成立日或之后的日期。'))
  fireEvent.change(screen.getByLabelText('语言'), { target: { value: 'en' } })
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(error))
})

it('distinguishes capital weights, carrying amounts and returned principal in both languages', async () => {
  const labels = [
    ['Capital Weight', '资金权重'],
    ['Modeled Capital Weight', '建模资金权重'],
    ['Carrying Amount', '账面金额'],
    ['Excluded Carrying Amount', '未建模账面金额'],
    ['Capital Returned', '本金返还金额'],
  ]
  render(<LanguageProvider><LanguageSelector />{labels.map(([en]) => <span key={en}>{en}</span>)}</LanguageProvider>)
  fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'zh-Hans' } })
  await waitFor(() => labels.forEach(([, zh]) => expect(screen.getByText(zh)).toBeInTheDocument()))
  fireEvent.change(screen.getByLabelText('语言'), { target: { value: 'en' } })
  await waitFor(() => labels.forEach(([en]) => expect(screen.getByText(en)).toBeInTheDocument()))
})

it('translates reference-price eligibility and amendment guidance in both languages', async () => {
  const labels = [
    ['Confirmed terms and audit evidence', '已确认条款与审计依据'],
    ['Identify the confirmed source and why these terms need correction.', '请说明已确认的资料来源，以及需要修订这些条款的原因。'],
    ['Carried at transaction cost; no observed market quote.', '按交易成本列账；暂无可观察市场报价。'],
    ['Market quote unavailable; quantity and book cost remain confirmed ledger facts.', '市场报价不可用；数量和账面成本仍为已确认账本事实。'],
    ['Initial purchase price; no independent market quote.', '初始买入价格；没有独立市场报价。'],
    ['Latest eligible market quote on or before the valuation date.', '估值日或之前最近的合格市场报价。'],
  ]
  render(<LanguageProvider><LanguageSelector />{labels.map(([en]) => <p key={en}>{en}</p>)}</LanguageProvider>)
  fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'zh-Hans' } })
  await waitFor(() => labels.forEach(([, zh]) => expect(screen.getByText(zh)).toBeInTheDocument()))
  fireEvent.change(screen.getByLabelText('语言'), { target: { value: 'en' } })
  await waitFor(() => labels.forEach(([en]) => expect(screen.getByText(en)).toBeInTheDocument()))
})

it('translates risk exclusions and FX requirements while preserving security names', async () => {
  const source = 'Current risk cannot treat unmodeled market exposure PDD Holdings Inc. as zero risk. · Forward RC requires an FX total-return series for non-base monetary exposure Cash (USD) (USD versus HKD).'
  render(<LanguageProvider><LanguageSelector /><span title={source}>Daily risk basis</span></LanguageProvider>)
  fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'zh-Hans' } })
  await waitFor(() => expect(screen.getByText('日频风险口径').title).toBe('当前风险不能将未能建模的市场敞口 PDD Holdings Inc. 视为零风险。 · 前瞻风险贡献需要非本位币货币敞口 Cash (USD) 的汇率总回报序列（USD 兑 HKD）。'))
  fireEvent.change(screen.getByLabelText('语言'), { target: { value: 'en' } })
  await waitFor(() => expect(screen.getByText('Daily risk basis').title).toBe(source))
})

describe('FCN risk translations', () => {
  it('keeps English source text in English mode and restores it after switching languages', async () => {
    render(
      <LanguageProvider>
        <LanguageSelector />
        <span>Delivery buffer</span>
        <span>10.00% above delivery strike</span>
      </LanguageProvider>,
    )

    expect(screen.getByText('Delivery buffer')).toBeTruthy()
    expect(screen.getByText('10.00% above delivery strike')).toBeTruthy()
    expect(screen.queryByText('距接票价')).toBeNull()

    fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'zh-Hans' } })
    await waitFor(() => {
      expect(screen.getByText('距接票价')).toBeTruthy()
      expect(screen.getByText('10.00% 高于接票价')).toBeTruthy()
    })

    fireEvent.change(screen.getByLabelText('语言'), { target: { value: 'en' } })
    await waitFor(() => {
      expect(screen.getByText('Delivery buffer')).toBeTruthy()
      expect(screen.getByText('10.00% above delivery strike')).toBeTruthy()
      expect(screen.queryByText('距接票价')).toBeNull()
    })
  })
})

it('translates FCN and option account prerequisites and all call/put opening and closing actions', async () => {
  const copy = [
    ['No FCN holding account', '暂无 FCN 持仓账户'],
    ['No Option holding account', '暂无期权持仓账户'],
    ['Enable FCN on a holding account with a same-currency settlement account.', '请先设置 FCN 持仓账户，并关联同币种结算账户。'],
    ['Enable Option on a holding account with a same-currency settlement account.', '请先设置期权持仓账户，并关联同币种结算账户。'],
    ['Buy to Open Call', '买入开仓看涨期权'],
    ['Sell to Open Call', '卖出开仓看涨期权'],
    ['Sell to Close Call', '卖出平仓看涨期权'],
    ['Buy to Close Call', '买入平仓看涨期权'],
    ['Buy to Open Put', '买入开仓看跌期权'],
    ['Sell to Open Put', '卖出开仓看跌期权'],
    ['Sell to Close Put', '卖出平仓看跌期权'],
    ['Buy to Close Put', '买入平仓看跌期权'],
  ]
  render(<LanguageProvider><LanguageSelector />{copy.map(([en]) => <span key={en}>{en}</span>)}</LanguageProvider>)
  fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'zh-Hans' } })
  await waitFor(() => { for (const [, zh] of copy) expect(screen.getByText(zh)).toBeTruthy() })
  fireEvent.change(screen.getByLabelText('语言'), { target: { value: 'en' } })
  await waitFor(() => { for (const [en] of copy) expect(screen.getByText(en)).toBeTruthy() })
})

it.each([
  ['call', '看涨期权'], ['put', '看跌期权'], [null, '期权'],
] as const)('translates actual %s option actions without changing their saved values', async (optionType, name) => {
  const actions = transactionActionGroups('derivative', 'option', optionType).flatMap(group => group.actions)
  const expected: Record<string, string> = {
    buy_to_open: `买入开仓${name}`, sell_to_open: `卖出开仓${name}`,
    sell_to_close: `卖出平仓${name}`, buy_to_close: `买入平仓${name}`,
    exercise_long: `买方${name}行权`, expire_long: `买方${name}到期`,
    cash_settle_long: `买方${name}现金结算`, assign_written: `卖方${name}被行权`,
    expire_written: `卖方${name}到期`, cash_settle_written: `卖方${name}现金结算`,
  }
  render(<LanguageProvider><LanguageSelector /><select defaultValue="exercise_long">
    {actions.map(action => <option key={action.value} value={action.value}>{action.label}</option>)}
  </select></LanguageProvider>)
  fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'zh-Hans' } })
  await waitFor(() => {
    for (const [value, label] of Object.entries(expected)) {
      expect((screen.getByRole('option', { name: label }) as HTMLOptionElement).value).toBe(value)
    }
  })
  fireEvent.change(screen.getByLabelText('语言'), { target: { value: 'en' } })
  await waitFor(() => {
    for (const action of actions) expect(screen.getByRole('option', { name: action.label })).toBeTruthy()
  })
})

it('translates singular, plural and filtered transaction activity counts', async () => {
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, 'zh-Hans')
  render(<LanguageProvider><span>1 activity</span><span>2 activities</span><span>1 matching activity</span><span>12 matching activities</span></LanguageProvider>)
  await waitFor(() => {
    expect(screen.getByText('1 项活动')).toBeTruthy()
    expect(screen.getByText('2 项活动')).toBeTruthy()
    expect(screen.getByText('1 项匹配活动')).toBeTruthy()
    expect(screen.getByText('12 项匹配活动')).toBeTruthy()
  })
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, 'en')
})

it('keeps account field labels separate from count classifiers and uses account date meanings', async () => {
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, 'zh-Hans')
  render(<LanguageProvider><span>Holding account</span><span>1 holding account</span><span>2 holding accounts</span><span>Account Opening Date</span><span>Account Closing Date</span></LanguageProvider>)
  await waitFor(() => {
    expect(screen.getByText('持仓账户')).toBeTruthy()
    expect(screen.getByText('1 个持仓账户')).toBeTruthy()
    expect(screen.getByText('2 个持仓账户')).toBeTruthy()
    expect(screen.getByText('开户日期')).toBeTruthy()
    expect(screen.getByText('关闭日期')).toBeTruthy()
    expect(screen.queryByText('个持仓账户')).toBeNull()
  })
})

it('translates only declared currency-qualified system fields and keeps every currency code', async () => {
  render(
    <LanguageProvider>
      <LanguageSelector />
      <span>Base Value (USD)</span>
      <span>FX Cost Basis (HKD)</span>
      <span>Options Subtotal (JPY)</span>
      <span>Custom Value (USD)</span>
      <span>Cash (USD)</span>
      <span>Base Value (customer currency)</span>
    </LanguageProvider>,
  )
  fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'zh-Hans' } })
  await waitFor(() => {
    expect(screen.getByText('本位币价值 (USD)')).toBeTruthy()
    expect(screen.getByText('汇兑成本基础 (HKD)')).toBeTruthy()
    expect(screen.getByText('期权小计 (JPY)')).toBeTruthy()
  })
  // Cash is translated only by the holdings component after checking canonical identity.
  for (const name of ['Custom Value (USD)', 'Cash (USD)', 'Base Value (customer currency)']) {
    expect(screen.getByText(name)).toBeTruthy()
  }
})
