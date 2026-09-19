import { act, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'

const mocks = vi.hoisted(() => ({ bootstrap: vi.fn(), routeLoaded: vi.fn(), clearCache: vi.fn() }))
vi.mock('./lib/bootstrap', () => ({ getPortfolioBootstrap: mocks.bootstrap }))
vi.mock('./lib/api', () => ({ clearPortfolioApiCache: mocks.clearCache }))
vi.mock('./pages/RiskPage', () => { mocks.routeLoaded(); return { default: () => <div>Authorized portfolio risk</div> } })

function initial(access: Record<string, unknown> = {}) {
  return { user_id: 'alice', session_id: 'one', capabilities: { research_enabled: true },
    access: { user_id: 'alice', portfolio_id: 'a', can_read: true, ...access } }
}

beforeEach(() => { mocks.bootstrap.mockReset(); mocks.clearCache.mockClear() })

describe('Portfolio startup', () => {
  it('downloads the current route while one identity request is pending, without mounting private content', async () => {
    let finish!: (value: ReturnType<typeof initial>) => void
    mocks.bootstrap.mockImplementation(() => new Promise(resolve => { finish = resolve }))
    render(<MemoryRouter initialEntries={['/portfolios/a/risk']}><App /></MemoryRouter>)
    await waitFor(() => expect(mocks.routeLoaded).toHaveBeenCalled())
    expect(mocks.bootstrap).toHaveBeenCalledTimes(1)
    expect(mocks.bootstrap).toHaveBeenCalledWith('a', expect.any(AbortSignal))
    expect(screen.queryByText('Authorized portfolio risk')).not.toBeInTheDocument()
    await act(async () => finish(initial()))
    expect(await screen.findByText('Authorized portfolio risk')).toBeInTheDocument()
    expect(mocks.bootstrap).toHaveBeenCalledTimes(1)
  })

  it.each([{ portfolio_id: 'other' }, { user_id: 'bob' }, { can_read: false }])('refuses a mismatched or unreadable access response: %o', async (access) => {
    mocks.bootstrap.mockResolvedValue(initial(access))
    render(<MemoryRouter initialEntries={['/portfolios/a/risk']}><App /></MemoryRouter>)
    expect(await screen.findByRole('alert')).toHaveTextContent('组合不存在或无权访问')
    expect(screen.queryByText('Authorized portfolio risk')).not.toBeInTheDocument()
  })
})
