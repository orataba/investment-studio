import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it } from 'vitest'
import userEvent from '@testing-library/user-event'
import { LANGUAGE_STORAGE_KEY, LanguageProvider, LanguageSelector } from '../../../../packages/ui/src/i18n'
import InfoHint from './components/InfoHint'
import QualityWarningsNotice from './components/QualityWarningsNotice'

it('translates risk input errors while retaining instrument names and model values', async () => {
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, 'zh-Hans')
  const messages = [
    'Current risk requires a return currency for Price.',
    'Current risk requires a current portfolio weight for Portfolio.',
    'Current risk requires full-history return series for Risk.',
    'Current risk requires base-currency total returns; Price is USD while the portfolio base currency is CNY.',
    'Production forward risk contribution is missing for Price.',
    'Production forward risk contribution is missing current weight for Portfolio.',
    'Production forward risk shares cannot be normalized for the modeled risk sleeve; got 0.00%.',
    'Production forward risk shares must aggregate to 100% before taxonomy grouping; got 94.50%.',
    'SAA risk target gap is missing current risk share for Risk.',
  ]
  render(<LanguageProvider><LanguageSelector />{messages.map(message => <p key={message}>{message}</p>)}</LanguageProvider>)
  const translated = [
    '缺少 Price 的收益币种，无法计算当前风险。',
    '缺少 Portfolio 的当前组合权重，无法计算当前风险。',
    '缺少 Risk 的完整历史收益序列，无法计算当前风险。',
    '当前风险计算需要以组合本位币 CNY 表示的总回报；Price 的收益币种为 USD。',
    '当前模型缺少 Price 的前瞻风险贡献。',
    '缺少 Portfolio 的当前权重，无法计算前瞻风险贡献。',
    '当前模型的风险贡献占比合计为 0.00%，无法归一化。',
    '分类汇总前，风险贡献占比应合计为 100%；当前合计为 94.50%。',
    '缺少 Risk 的当前风险贡献占比，无法比较 SAA 风险目标。',
  ]
  await waitFor(() => translated.forEach(message => expect(screen.getByText(message)).toBeVisible()))
  fireEvent.change(screen.getByLabelText('语言'), { target: { value: 'en' } })
  await waitFor(() => messages.forEach(message => expect(screen.getByText(message)).toBeVisible()))
})

it('shows the blocking market-data date and required prices or FX in Chinese', async () => {
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, 'zh-Hans')
  render(<LanguageProvider>
    <span>Total Portfolio Operational Return</span>
    <span>Arithmetic Return Contribution</span>
    <span>Linked Return Contribution</span>
    <span>Linked contributions sum to period TWR; child contributions sum to their parent.</span>
    <span>Daily risk basis</span>
    <span>No effective analytics taxonomy selection is configured.</span>
    <span>Correlation coverage</span>
    <span>Benchmark comparison is unavailable because 2 eligible portfolio return dates are missing from benchmark history</span>
    <span>Benchmark comparison is unavailable because required observations are missing: 2026-09-08. No official market calendar is available to confirm closures.</span>
    <span>Price-return comparator. close · confirmed price-return basis (close). Comparison uses the selected price-return series. Distributions are excluded from this benchmark, so relative results include that difference.</span>
    <QualityWarningsNotice warnings={[
      'Required market data missing: 2026-09-01; stock-a valuation price, FX USD/CNY. Supply the required observation before performance can continue.',
    ]} />
  </LanguageProvider>)
  await userEvent.click(screen.getByRole('button', { name: /Data quality warning|数据质量提示/ }))
  await waitFor(() => {
    expect(screen.getByText('组合账面收益')).toBeVisible()
    expect(screen.getByText('收益贡献（算术）')).toBeVisible()
    expect(screen.getByText('收益贡献（复利链接）')).toBeVisible()
    expect(screen.getByText('各组复利链接贡献合计 = 期间 TWR；组内明细合计 = 所属分组贡献。')).toBeVisible()
    expect(screen.getByText('日频风险口径')).toBeVisible()
    expect(screen.getByText('尚未配置生效的分析分类体系。')).toBeVisible()
    expect(screen.getByText('相关性覆盖')).toBeVisible()
    expect(screen.getByText('基准历史缺少 2 个组合有效收益日期，暂时无法比较')).toBeVisible()
    expect(screen.getByText(/基准缺少必要日期的价格/)).toHaveTextContent('尚无官方交易日历，不能将价格缺口视为休市')
    expect(screen.getByText(/已确认的价格回报口径（收盘价）/)).toHaveTextContent('该基准不包含分红，相对结果包含这一口径差异')
    expect(screen.getByText('缺少 2026-09-01 的必要行情：stock-a 估值价格, USD/CNY 汇率。补齐该日数据后才能继续计算绩效。')).toBeVisible()
  })
  window.localStorage.removeItem(LANGUAGE_STORAGE_KEY)
})

it('translates a labeled multi-sentence explanation without losing its full-message mapping', async () => {
  const detail = 'Operational return is used for NAV reconciliation because event-valued assets and obligations remain on carrying basis. Market Risk Watch uses a separate return chain that models derivatives and base-currency cash at zero return; benchmark overlays are therefore hidden from this TWR chart.'
  const translated = '按事件估值的资产与负债仍使用账面价值，因此净值核对使用账面估值收益。市场风险监控使用独立收益序列，衍生品及本位币现金按零收益建模，所以此时间加权收益图不叠加基准。'
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, 'zh-Hans')
  render(<LanguageProvider>
    <LanguageSelector />
    <InfoHint label="Operational performance basis" detail={detail} />
  </LanguageProvider>)
  const hint = await screen.findByRole('button', { name: `账面收益计算口径：${translated}` })
  fireEvent.click(hint)
  await waitFor(() => expect(screen.getByRole('tooltip')).toHaveTextContent(translated))

  fireEvent.change(screen.getByLabelText('语言'), { target: { value: 'en' } })
  await waitFor(() => {
    expect(hint).toHaveAccessibleName(`Operational performance basis: ${detail}`)
    expect(screen.getByRole('tooltip')).toHaveTextContent(detail)
  })
})
