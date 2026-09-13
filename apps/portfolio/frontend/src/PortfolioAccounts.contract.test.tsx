import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import PortfolioAccessProvider from './components/PortfolioAccessProvider'
import PortfolioSessionProvider from './components/PortfolioSessionProvider'
import PortfolioMembersSettings from './components/PortfolioMembersSettings'
import { LanguageProvider } from '../../../../packages/ui/src/i18n'

const api = vi.hoisted(() => ({ clearPortfolioApiCache: vi.fn(), getPortfolioAccess: vi.fn(), getPortfolioSession: vi.fn(), getPortfolioMembers: vi.fn(), getPortfolioMemberCandidates: vi.fn(), setPortfolioMember: vi.fn(), removePortfolioMember: vi.fn() }))
vi.mock('./lib/api', () => api)

beforeEach(() => vi.resetAllMocks())
const member = { user_id: 'alice', display_name: '甲经理', role: 'manager', granted_by: 'alice', granted_at: '2026-09-08' }

describe('Portfolio account boundaries', () => {
  it('does not mount private portfolio content until authorization succeeds', async () => {
    api.getPortfolioAccess.mockRejectedValue(new Error('组合不存在或无权访问'))
    render(<MemoryRouter initialEntries={['/portfolios/a/holdings']}><Routes><Route path="/portfolios/:portfolioId/holdings" element={<PortfolioAccessProvider><div>私人持仓</div></PortfolioAccessProvider>} /></Routes></MemoryRouter>)
    expect(screen.queryByText('私人持仓')).not.toBeInTheDocument()
    expect(await screen.findByRole('alert')).toHaveTextContent('组合不存在或无权访问')
    expect(screen.queryByText('私人持仓')).not.toBeInTheDocument()
  })

  it('clears cached data and unmounts old account content when the login changes', async () => {
    api.getPortfolioSession.mockResolvedValueOnce({ user_id: 'alice', display_name: '甲经理', can_create: true }).mockRejectedValueOnce(new Error('请先登录'))
    render(<PortfolioSessionProvider><div>旧账号的工作区</div></PortfolioSessionProvider>)
    await screen.findByText('旧账号的工作区')
    fireEvent(window, new Event('focus'))
    expect(screen.getByText('旧账号的工作区')).toBeInTheDocument()
    expect(await screen.findByRole('alert')).toHaveTextContent('请先登录')
    expect(screen.queryByText('旧账号的工作区')).not.toBeInTheDocument()
    expect(api.clearPortfolioApiCache).toHaveBeenCalledTimes(2)
  })

  it('keeps the current page and unsaved input mounted during an unchanged focus check', async () => {
    const session = { user_id: 'alice', session_id: 'session-a', display_name: '甲经理', can_create: true }
    const access = { portfolio_id: 'a', team_id: 'team-a', role: 'manager', can_read: true, can_edit: true, can_manage: true }
    let finishSession = () => {}
    let finishAccess = () => {}
    api.getPortfolioSession.mockResolvedValueOnce(session).mockImplementationOnce(() => new Promise(resolve => { finishSession = () => resolve({ ...session }) }))
    api.getPortfolioAccess.mockResolvedValueOnce(access).mockImplementationOnce(() => new Promise(resolve => { finishAccess = () => resolve({ ...access }) }))
    render(<PortfolioSessionProvider><MemoryRouter initialEntries={['/portfolios/a/holdings']}><Routes><Route path="/portfolios/:portfolioId/holdings" element={<PortfolioAccessProvider><input aria-label="未保存筛选" /></PortfolioAccessProvider>} /></Routes></MemoryRouter></PortfolioSessionProvider>)
    const input = await screen.findByRole('textbox')
    fireEvent.change(input, { target: { value: '中芯国际' } })
    fireEvent(window, new Event('focus'))
    expect(screen.getByRole('textbox')).toBe(input)
    expect(input).toHaveValue('中芯国际')
    await act(async () => { finishSession(); finishAccess() })
    expect(screen.getByRole('textbox')).toBe(input)
    expect(input).toHaveValue('中芯国际')
    expect(api.clearPortfolioApiCache).toHaveBeenCalledTimes(1)
  })

  it('remounts account-bound state after a different session is confirmed', async () => {
    api.getPortfolioSession.mockResolvedValueOnce({ user_id: 'alice', session_id: 'session-a' }).mockResolvedValueOnce({ user_id: 'bob', session_id: 'session-b' })
    render(<PortfolioSessionProvider><input aria-label="账号内草稿" defaultValue="" /></PortfolioSessionProvider>)
    const input = await screen.findByRole('textbox')
    fireEvent.change(input, { target: { value: '旧账号草稿' } })
    fireEvent(window, new Event('focus'))
    await waitFor(() => expect(screen.getByRole('textbox')).not.toBe(input))
    expect(screen.getByRole('textbox')).toHaveValue('')
    expect(api.clearPortfolioApiCache).toHaveBeenCalledTimes(2)
  })

  it('immediately hides account content after an explicit unauthorized event', async () => {
    api.getPortfolioSession.mockResolvedValueOnce({ user_id: 'alice', session_id: 'session-a' }).mockImplementationOnce(() => new Promise(() => {}))
    render(<PortfolioSessionProvider><div>需要授权的内容</div></PortfolioSessionProvider>)
    await screen.findByText('需要授权的内容')
    fireEvent(window, new Event('studio-auth-changed'))
    expect(screen.queryByText('需要授权的内容')).not.toBeInTheDocument()
  })

  it('removes portfolio content when a background access check confirms revocation', async () => {
    api.getPortfolioAccess.mockResolvedValueOnce({ portfolio_id: 'a', role: 'manager', can_read: true }).mockRejectedValueOnce(new Error('组合访问权限已撤销'))
    render(<MemoryRouter initialEntries={['/portfolios/a/holdings']}><Routes><Route path="/portfolios/:portfolioId/holdings" element={<PortfolioAccessProvider><div>私人持仓</div></PortfolioAccessProvider>} /></Routes></MemoryRouter>)
    await screen.findByText('私人持仓')
    fireEvent(window, new Event('focus'))
    expect(await screen.findByRole('alert')).toHaveTextContent('组合访问权限已撤销')
    expect(screen.queryByText('私人持仓')).not.toBeInTheDocument()
  })

  it('ignores an older login response after a newer check has revoked access', async () => {
    let finishOld = () => {}
    api.getPortfolioSession.mockImplementationOnce(() => new Promise(resolve => {
      finishOld = () => resolve({ user_id: 'alice', display_name: '甲经理', can_create: true })
    })).mockRejectedValueOnce(new Error('会话已撤销'))
    render(<PortfolioSessionProvider><div>旧账号的工作区</div></PortfolioSessionProvider>)
    fireEvent(window, new Event('focus'))
    expect(await screen.findByRole('alert')).toHaveTextContent('会话已撤销')
    await act(async () => finishOld())
    expect(screen.queryByText('旧账号的工作区')).not.toBeInTheDocument()
  })

  it('grants an explicitly selected member a role and displays manager handover conflicts', async () => {
    api.getPortfolioMembers.mockResolvedValue({ members: [member] })
    api.getPortfolioMemberCandidates.mockResolvedValue({ members: [member, { user_id: 'bob', display_name: '乙经理' }] })
    api.setPortfolioMember.mockResolvedValue({})
    api.removePortfolioMember.mockRejectedValue(new Error('请先指定另一位管理者'))
    const user = userEvent.setup()
    render(<LanguageProvider enableDomTranslation={false}><PortfolioMembersSettings portfolioId="a" /></LanguageProvider>)
    await screen.findByText('甲经理')
    await user.selectOptions(screen.getByLabelText('Add member'), 'bob')
    await user.selectOptions(screen.getByLabelText('Role'), 'viewer')
    await user.click(screen.getByRole('button', { name: 'Grant access' }))
    await waitFor(() => expect(api.setPortfolioMember).toHaveBeenCalledWith('a', 'bob', 'viewer'))
    await user.click(screen.getByRole('button', { name: 'Remove access' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('请先指定另一位管理者')
  })

})
