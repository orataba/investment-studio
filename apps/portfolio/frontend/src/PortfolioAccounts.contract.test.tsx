import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import PortfolioAccessProvider from './components/PortfolioAccessProvider'
import PortfolioSessionProvider from './components/PortfolioSessionProvider'
import PortfolioBootstrapProvider from './components/PortfolioBootstrapProvider'
import { getPortfolioBootstrap } from './lib/bootstrap'
import PortfolioMembersSettings from './components/PortfolioMembersSettings'
import { LanguageProvider } from '../../../../packages/ui/src/i18n'

const api = vi.hoisted(() => ({ clearPortfolioApiCache: vi.fn(), getPortfolioMembers: vi.fn(), getPortfolioMemberCandidates: vi.fn(), setPortfolioMember: vi.fn(), removePortfolioMember: vi.fn() }))
vi.mock('./lib/api', () => api)
vi.mock('./lib/bootstrap', () => ({ getPortfolioBootstrap: vi.fn() }))
const bootstrap = vi.mocked(getPortfolioBootstrap)
const response = (session: Record<string, unknown> = {}, access: unknown = null) => ({ user_id: 'alice', session_id: 'session-a', capabilities: { research_enabled: true }, ...session, access: access ? { user_id: session.user_id || 'alice', ...access as object } : null }) as Awaited<ReturnType<typeof getPortfolioBootstrap>>
function Boundary({ children, portfolioId = null }: { children: React.ReactNode; portfolioId?: string | null }) {
  return <PortfolioBootstrapProvider portfolioId={portfolioId}><PortfolioSessionProvider>{children}</PortfolioSessionProvider></PortfolioBootstrapProvider>
}

beforeEach(() => vi.resetAllMocks())
const member = { user_id: 'alice', display_name: '甲经理', role: 'manager', granted_by: 'alice', granted_at: '2026-09-08' }

describe('Portfolio account boundaries', () => {
  it('does not mount private portfolio content until authorization succeeds', async () => {
    bootstrap.mockRejectedValue(new Error('组合不存在或无权访问'))
    render(<MemoryRouter initialEntries={['/portfolios/a/holdings']}><Routes><Route path="/portfolios/:portfolioId/holdings" element={<Boundary portfolioId="a"><PortfolioAccessProvider><div>私人持仓</div></PortfolioAccessProvider></Boundary>} /></Routes></MemoryRouter>)
    expect(screen.queryByText('私人持仓')).not.toBeInTheDocument()
    expect(await screen.findByRole('alert')).toHaveTextContent('组合不存在或无权访问')
    expect(screen.queryByText('私人持仓')).not.toBeInTheDocument()
  })

  it('clears cached data and unmounts old account content when the login changes', async () => {
    bootstrap.mockResolvedValueOnce(response({ user_id: 'alice', display_name: '甲经理', can_create: true })).mockRejectedValueOnce(new Error('请先登录'))
    render(<Boundary><div>旧账号的工作区</div></Boundary>)
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
    let finishBootstrap = () => {}
    bootstrap.mockResolvedValueOnce(response(session, access)).mockImplementationOnce(() => new Promise(resolve => { finishBootstrap = () => resolve(response(session, access)) }))
    render(<Boundary portfolioId="a"><MemoryRouter initialEntries={['/portfolios/a/holdings']}><Routes><Route path="/portfolios/:portfolioId/holdings" element={<PortfolioAccessProvider><input aria-label="未保存筛选" /></PortfolioAccessProvider>} /></Routes></MemoryRouter></Boundary>)
    const input = await screen.findByRole('textbox')
    fireEvent.change(input, { target: { value: '中芯国际' } })
    fireEvent(window, new Event('focus'))
    expect(screen.getByRole('textbox')).toBe(input)
    expect(input).toHaveValue('中芯国际')
    await act(async () => { finishBootstrap() })
    expect(screen.getByRole('textbox')).toBe(input)
    expect(input).toHaveValue('中芯国际')
    expect(api.clearPortfolioApiCache).toHaveBeenCalledTimes(1)
  })

  it('remounts account-bound state after a different session is confirmed', async () => {
    bootstrap.mockResolvedValueOnce(response()).mockResolvedValueOnce(response({ user_id: 'bob', session_id: 'session-b' }))
    render(<Boundary><input aria-label="账号内草稿" defaultValue="" /></Boundary>)
    const input = await screen.findByRole('textbox')
    fireEvent.change(input, { target: { value: '旧账号草稿' } })
    fireEvent(window, new Event('focus'))
    await waitFor(() => expect(screen.getByRole('textbox')).not.toBe(input))
    expect(screen.getByRole('textbox')).toHaveValue('')
    expect(api.clearPortfolioApiCache).toHaveBeenCalledTimes(2)
  })

  it('immediately hides account content after an explicit unauthorized event', async () => {
    bootstrap.mockResolvedValueOnce(response()).mockImplementationOnce(() => new Promise(() => {}))
    render(<Boundary><div>需要授权的内容</div></Boundary>)
    await screen.findByText('需要授权的内容')
    fireEvent(window, new Event('studio-auth-changed'))
    expect(screen.queryByText('需要授权的内容')).not.toBeInTheDocument()
  })

  it('removes portfolio content when a background access check confirms revocation', async () => {
    bootstrap.mockResolvedValueOnce(response({}, { portfolio_id: 'a', role: 'manager', can_read: true })).mockRejectedValueOnce(new Error('组合访问权限已撤销'))
    render(<MemoryRouter initialEntries={['/portfolios/a/holdings']}><Routes><Route path="/portfolios/:portfolioId/holdings" element={<Boundary portfolioId="a"><PortfolioAccessProvider><div>私人持仓</div></PortfolioAccessProvider></Boundary>} /></Routes></MemoryRouter>)
    await screen.findByText('私人持仓')
    fireEvent(window, new Event('focus'))
    expect(await screen.findByRole('alert')).toHaveTextContent('组合访问权限已撤销')
    expect(screen.queryByText('私人持仓')).not.toBeInTheDocument()
  })

  it('ignores an older login response after a newer check has revoked access', async () => {
    let finishOld = () => {}
    bootstrap.mockImplementationOnce(() => new Promise(resolve => {
      finishOld = () => resolve(response({ user_id: 'alice', display_name: '甲经理', can_create: true }))
    })).mockRejectedValueOnce(new Error('会话已撤销'))
    render(<Boundary><div>旧账号的工作区</div></Boundary>)
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

it('hides the previous portfolio immediately and ignores its late bootstrap after route changes', async () => {
  let finishOld!: (value: Awaited<ReturnType<typeof getPortfolioBootstrap>>) => void
  let finishNew!: (value: Awaited<ReturnType<typeof getPortfolioBootstrap>>) => void
  bootstrap.mockResolvedValueOnce(response({}, { portfolio_id: 'a', can_read: true }))
    .mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve }))
    .mockImplementationOnce(() => new Promise(resolve => { finishNew = resolve }))
  const view = render(<Boundary portfolioId="a"><div>Portfolio A</div></Boundary>)
  await screen.findByText('Portfolio A')
  fireEvent(window, new Event('focus'))
  view.rerender(<Boundary portfolioId="b"><div>Portfolio B</div></Boundary>)
  expect(screen.queryByText('Portfolio A')).not.toBeInTheDocument()
  expect(screen.queryByText('Portfolio B')).not.toBeInTheDocument()
  await act(async () => finishNew(response({}, { portfolio_id: 'b', can_read: true })))
  await screen.findByText('Portfolio B')
  await act(async () => finishOld(response({}, { portfolio_id: 'a', can_read: true })))
  expect(screen.getByText('Portfolio B')).toBeInTheDocument()
  expect(bootstrap.mock.calls.map(([portfolioId]) => portfolioId)).toEqual(['a', 'a', 'b'])
})
