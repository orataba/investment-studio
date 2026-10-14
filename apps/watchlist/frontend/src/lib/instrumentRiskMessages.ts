const reasons: Record<string, [string, string]> = {
  unconfirmed_economic_returns: ['收益口径尚未确认，暂不计算日度风险。', 'Economic return semantics are unconfirmed; daily risk is unavailable.'],
  non_daily_source: ['该资产没有已确认的日频收益，暂不计算日度 VaR / ES。', 'This asset has no confirmed daily returns; daily VaR / ES are unavailable.'],
  unverified_daily_calendar: ['日频交易日历尚未确认，暂不计算日度风险。', 'The daily trading calendar is unverified; daily risk is unavailable.'],
  missing_daily_return_intervals: ['实际样本内部或尾部存在缺失／不可用收益区间，暂不计算 VaR / ES。', 'Missing or unavailable intervals within or after the actual sample prevent VaR / ES.'],
  incomplete_market_session: ['存在尚未收盘的行情日期，暂不使用该收益样本。', 'The return sample contains a market session that has not closed.'],
  invalid_return_interval: ['收益区间存在无效或重复记录，暂不计算日度风险。', 'Invalid or duplicate return intervals prevent a daily estimate.'],
  less_than_one_tail_observation: ['所选置信度的尾部不足一个观察样本，暂不显示 VaR / ES。', 'The selected tail contains less than one observation; VaR / ES are unavailable.'],
  no_verified_daily_returns: ['所选区间没有有效日频收益，暂不计算。', 'No verified daily returns are available in this window.'],
}

export function riskUnavailableReason(reason: string, zh: boolean): string {
  return reasons[reason]?.[zh ? 0 : 1] ?? reason
}
