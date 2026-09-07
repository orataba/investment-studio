// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import EtfProfilePanel from './EtfProfilePanel'

const reference = vi.hoisted(() => vi.fn())
vi.mock('../lib/api', () => ({ getInstrumentReferenceData: reference }))
beforeEach(() => { reference.mockResolvedValue({ instrument_id: 'xlk', instrument_type: 'etf', provider: 'fmp', provider_symbol: 'XLK', fetched_at: '2026-09-06T13:14:45Z', source: {}, section_errors: {}, sections: {
  fund_info: { etfCompany: 'SPDR', assetClass: 'Equity', expenseRatio: 0.08, assetsUnderManagement: 121079860000, navCurrency: 'USD', holdingsCount: 73, inceptionDate: '1998-12-16', domicile: 'US', website: 'https://example.com/fund', description: '基金通过复制指数持仓跟踪目标市场。', updatedAt: '2026-09-05T23:19:30Z' },
  holdings: [{ asset: 'NVDA', name: 'NVIDIA CORP', weightPercentage: 14.91015524, updatedAt: '2026-09-05 13:48:44' }, { asset: 'USD', name: 'US Dollar', weightPercentage: 0.03, updatedAt: '2026-09-05 13:48:44' }],
  sector_weights: [{ sector: 'Technology', weightPercentage: 99.82 }], country_weights: [{ country: 'United States', weightPercentage: '97.23%' }],
} }) })
afterEach(() => { cleanup(); vi.resetAllMocks() })

it.each(['xlk', 'spy'])('shows %s through the same registered ETF reference path with source fees and holdings counts', async (id) => {
  render(<EtfProfilePanel instrumentId={id} language="zh-Hans" />)
  await screen.findByText('SPDR')
  expect(reference).toHaveBeenCalledWith(id)
  expect(screen.getByText('总费率').parentElement?.textContent).toBe('总费率0.08%')
  expect(screen.getByText('披露持仓数').parentElement?.textContent).toBe('披露持仓数73')
  expect(screen.getByText('成立日期').parentElement?.textContent).toBe('成立日期1998-12-16')
  expect(screen.getByText('资产规模').parentElement?.textContent).toContain('规模币种未单独披露')
  expect(screen.getByRole('link', { name: '基金官网 ↗' }).getAttribute('href')).toBe('https://example.com/fund')
  fireEvent.click(screen.getByText('持仓与分布 · 已保存 2 行'))
  const table = screen.getByRole('table')
  expect(within(table).getByText('NVIDIA CORP')).toBeTruthy()
  expect(within(table).getByText('14.91%')).toBeTruthy()
  expect(within(table).getAllByText('未披露')).toHaveLength(2)
  expect(within(table).getAllByText('2026-09-05 13:48:44 （时区未披露）')).toHaveLength(2)
  expect(screen.getByText('当前仅保存部分明细（2 行），基金资料披露 73 项持仓。')).toBeTruthy()
  expect(screen.getByText('97.23%')).toBeTruthy()
  expect(screen.getByText('99.82%')).toBeTruthy()
})

it('uses the normal mainland ETF fee and disclosed report dates without calling management fees total expenses', async () => {
  reference.mockResolvedValue({ instrument_id: 'cn-etf', instrument_type: 'etf', provider: 'tushare', provider_symbol: '510300.SH', fetched_at: '2026-09-06T13:00:00Z', source: {}, section_errors: {}, sections: {
    fund_info: { management: '测试基金公司', fund_type: '股票型', found_date: '20120504', m_fee: 0.5, c_fee: 0.1, benchmark: '沪深300指数' },
    holdings: [{ symbol: '600519.SH', stk_mkv_ratio: 4.8, end_date: '20260630', ann_date: '20260831' }],
  } })
  render(<EtfProfilePanel instrumentId="cn-etf" language="zh-Hans" />)
  await screen.findByText('测试基金公司')
  expect(screen.getByText('管理费率').parentElement?.textContent).toBe('管理费率0.5%')
  expect(screen.queryByText('总费率')).toBeNull()
  fireEvent.click(screen.getByText('持仓与分布 · 已保存 1 行'))
  const table = screen.getByRole('table')
  expect(within(table).getByText('2026-06-30')).toBeTruthy()
  expect(within(table).getByText('2026-08-31')).toBeTruthy()
  expect(within(table).getByText('占股票市值比')).toBeTruthy()
})

it('keeps missing reference fields explicitly unavailable instead of filling ETF defaults', async () => {
  reference.mockResolvedValue({ instrument_id: 'missing', instrument_type: 'etf', provider: 'unavailable', provider_symbol: null, fetched_at: null, source: {}, sections: {}, section_errors: { reference: '尚未采集资料' } })
  render(<EtfProfilePanel instrumentId="missing" language="zh-Hans" />)
  expect(await screen.findByText('尚未取得基金概况，不能据此推断基金结构。')).toBeTruthy()
  expect(screen.queryByText('0.08%')).toBeNull()
  expect(screen.queryByText('披露持仓数')).toBeNull()
  fireEvent.click(screen.getByText('资料覆盖限制'))
  expect(screen.getByText('尚未采集资料')).toBeTruthy()
})
