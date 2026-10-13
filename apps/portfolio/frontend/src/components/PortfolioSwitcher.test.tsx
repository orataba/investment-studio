import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { MemoryRouter, useLocation, useNavigate } from 'react-router'
import { LanguageProvider } from '../../../../../packages/ui/src/i18n'
import PortfolioSwitcher from './PortfolioSwitcher'

function Fixture({ count }: { count: number }) {
  const location = useLocation()
  const navigate = useNavigate()
  const options = Array.from({ length: count }, (_, index) => ({ portfolio_id: `${index + 1}`, portfolio_name: index === 20 ? '跨市场长期组合名称完整展示 — Global diversified investment portfolio' : `Portfolio ${index + 1}` }))
  return <><PortfolioSwitcher portfolios={options} currentId={location.pathname.split('/')[2]} /><output>{location.pathname}</output><button onClick={() => navigate(-1)}>Back</button></>
}

describe('compact portfolio switching', () => {
  it.each([10, 30])('keeps %s portfolios collapsed with a searchable keyboard path and back navigation', async (count) => {
    const user = userEvent.setup()
    render(<LanguageProvider enableDomTranslation={false}><MemoryRouter initialEntries={['/portfolios/1/overview']}><Fixture count={count} /></MemoryRouter></LanguageProvider>)
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Switch portfolio: Portfolio 1' }))
    const search = screen.getByRole('combobox', { name: 'Search portfolios' })
    expect(search).toHaveFocus()
    expect(within(screen.getByRole('listbox')).getAllByRole('option')).toHaveLength(count)
    await user.keyboard('{ArrowDown}')
    expect(search).toHaveAttribute('aria-activedescendant', screen.getByRole('option', { name: 'Portfolio 2 2' }).id)
    await user.keyboard('{ArrowUp}')
    await user.type(search, 'Portfolio 2')
    await user.keyboard('{Enter}')
    expect(screen.getByRole('status')).toHaveTextContent('/portfolios/2/overview')
    expect(screen.getByRole('button', { name: 'Switch portfolio: Portfolio 2' })).toHaveFocus()
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Back' }))
    expect(screen.getByRole('button', { name: 'Switch portfolio: Portfolio 1' })).toBeInTheDocument()
  })

  it('exposes long names, empty matches and Escape focus restoration without selecting', async () => {
    const user = userEvent.setup()
    render(<LanguageProvider enableDomTranslation={false}><MemoryRouter initialEntries={['/portfolios/1/overview']}><Fixture count={30} /></MemoryRouter></LanguageProvider>)
    const trigger = screen.getByRole('button', { name: 'Switch portfolio: Portfolio 1' })
    await user.click(trigger)
    const search = screen.getByRole('combobox')
    await user.type(search, '跨市场')
    expect(screen.getByRole('option')).toHaveTextContent('跨市场长期组合名称完整展示 — Global diversified investment portfolio')
    await user.clear(search)
    await user.type(search, 'no such portfolio')
    expect(screen.getByText('No matching portfolios')).toBeInTheDocument()
    expect(search).not.toHaveAttribute('aria-activedescendant')
    await user.keyboard('{Enter}{Escape}')
    expect(trigger).toHaveFocus()
    expect(screen.getByRole('status')).toHaveTextContent('/portfolios/1/overview')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })
})
