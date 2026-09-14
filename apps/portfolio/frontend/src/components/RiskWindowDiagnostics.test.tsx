import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { LanguageProvider } from '../../../../../packages/ui/src/i18n'
import type { RiskWindowDiagnostics as Diagnostics } from '../lib/riskWindowData'
import RiskWindowDiagnostics from './RiskWindowDiagnostics'

const diagnostics: Diagnostics = {
  status: 'unavailable', asOfDate: '2026-09-10', windowStartDate: '2026-06-10',
  observedStartDate: '2026-06-23', observedEndDate: '2026-09-10', periodStartDate: '2026-06-22',
  observationCount: 58, requiredObservationCount: 45, scopeMemberCount: 1,
  issues: [{ memberKey: 'portfolio', memberLabel: 'Portfolio', reason: 'window_coverage',
    coverageReason: 'Risk window lacks a valid daily start anchor near 2026-06-10.', missingDates: [], missingDateCount: 0 }],
}

describe('risk window explanations', () => {
  it('distinguishes insufficient starting history from a low observation count in Chinese', async () => {
    window.history.replaceState({}, '', '/?lang=zh-Hans')
    render(<LanguageProvider enableDomTranslation={false}><RiskWindowDiagnostics diagnostics={diagnostics} /></LanguageProvider>)
    expect(screen.getByRole('status')).toHaveTextContent('缺少 2026-06-10 附近的有效起始观测')
    expect(screen.getByLabelText('分析样本')).toHaveTextContent('观测日期数 58')
    expect(screen.getByLabelText('分析样本')).toHaveTextContent('2026-06-23')
    expect(screen.getByLabelText('分析样本')).not.toHaveTextContent('2026-06-22')
    await userEvent.click(screen.getByText('查看原因与影响标的'))
    expect(screen.queryByText(/Risk window lacks/)).not.toBeInTheDocument()
  })

  it('explains a single member without describing it as a missing history problem', () => {
    window.history.replaceState({}, '', '/?lang=zh-Hans')
    render(<LanguageProvider enableDomTranslation={false}><RiskWindowDiagnostics diagnostics={{ ...diagnostics,
      issues: [{ memberKey: 'scope', memberLabel: 'Selected Scope', reason: 'scope_unavailable',
        coverageReason: 'Correlation comparison requires at least two members in the selected scope.', missingDates: [], missingDateCount: 0 }],
    }} /></LanguageProvider>)
    expect(screen.getByRole('status')).toHaveTextContent('此范围不足两个成员，无法进行相关性比较')
    expect(screen.getByRole('status')).not.toHaveTextContent('历史未完整')
  })
})
