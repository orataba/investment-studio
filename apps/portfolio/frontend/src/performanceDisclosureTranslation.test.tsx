import { render, screen, waitFor } from '@testing-library/react'
import { expect, it } from 'vitest'
import userEvent from '@testing-library/user-event'
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from '../../../../packages/ui/src/i18n'
import QualityWarningsNotice from './components/QualityWarningsNotice'

it('shows the blocking market-data date and required prices or FX in Chinese', async () => {
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, 'zh-Hans')
  render(<LanguageProvider>
    <span>Total Portfolio Operational Return</span>
    <span>Arithmetic Return Contribution</span>
    <span>Linked Return Contribution</span>
    <span>Linked contributions sum to period TWR; child contributions sum to their parent.</span>
    <span>Benchmark comparison is unavailable because 2 eligible portfolio return dates are missing from benchmark history</span>
    <span>Benchmark comparison is unavailable because required observations are missing: 2026-09-08. No official market calendar is available to confirm closures.</span>
    <span>Price-return comparator. close · confirmed price-return basis (close). Comparison uses the selected price-return series. Distributions are excluded from this benchmark, so relative results include that difference.</span>
    <QualityWarningsNotice warnings={[
      'Required market data missing: 2026-09-01; stock-a valuation price, FX USD/CNY. Supply the required observation before performance can continue.',
    ]} />
  </LanguageProvider>)
  await userEvent.click(screen.getByRole('button', { name: /Data quality warning|数据质量提示/ }))
  await waitFor(() => {
    expect(screen.getByText('组合经营回报（账面估值）')).toBeVisible()
    expect(screen.getByText('收益贡献（算术）')).toBeVisible()
    expect(screen.getByText('收益贡献（复利链接）')).toBeVisible()
    expect(screen.getByText('各组复利链接贡献合计 = 期间 TWR；组内明细合计 = 所属分组贡献。')).toBeVisible()
    expect(screen.getByText('基准历史缺少 2 个组合有效收益日期，暂时无法比较')).toBeVisible()
    expect(screen.getByText(/基准缺少必要日期的价格/)).toHaveTextContent('尚无官方交易日历，不能将价格缺口视为休市')
    expect(screen.getByText(/已确认的价格回报口径（收盘价）/)).toHaveTextContent('该基准不包含分红，相对结果包含这一口径差异')
    expect(screen.getByText('缺少 2026-09-01 的必要行情：stock-a 估值价格, USD/CNY 汇率。补齐该日数据后才能继续计算绩效。')).toBeVisible()
  })
  window.localStorage.removeItem(LANGUAGE_STORAGE_KEY)
})
