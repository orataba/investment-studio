// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LanguageProvider } from '../../../packages/ui/src/i18n'
import AccountPage from './AccountPage'
import type { Account, Member } from './accountApi'
import { homeMessages } from './messages'

const request = vi.hoisted(() => vi.fn())
vi.mock('./accountApi', async importOriginal => ({ ...await importOriginal<object>(), accountRequest: request }))
const owner: Account = { user_id: 'owner', username: 'owner', display_name: 'Owner', team_name: 'Team', team_role: 'admin', is_team_owner: true }
const member: Member = { user_id: 'member', username: 'analyst', display_name: 'Analyst', role: 'member', active: true, status: 'active', is_team_owner: false }
let failMutation = false
let failRefresh = false
beforeEach(() => {
  window.history.replaceState({}, '', '/account?lang=en')
  failMutation = false; failRefresh = false
  request.mockImplementation(async (path: string, method = 'GET') => {
    if (method !== 'GET') {
      if (failMutation) throw new Error('Access denied')
      return path.endsWith('/reset') ? { activation_url: 'https://example.test/activate?token=fixture' } : {}
    }
    if (failRefresh) throw new Error('Service unavailable')
    return path === '/session' ? owner : { members: [member] }
  })
})
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.restoreAllMocks() })
async function open() {
  render(<LanguageProvider messages={homeMessages}><AccountPage /></LanguageProvider>)
  await screen.findByRole('combobox', { name: 'Team role for Analyst' })
}
const writes = () => request.mock.calls.filter(([, method]) => method && method !== 'GET')

it('requires the named role change confirmation, preserves a profile draft, and sends only that change', async () => {
  await open()
  const profile = screen.getByRole('textbox', { name: 'Username' }).closest('form')!
  fireEvent.change(within(profile).getByRole('textbox', { name: 'Display name' }), { target: { value: 'Unsaved profile' } })
  const role = screen.getByRole('combobox', { name: 'Team role for Analyst' })
  fireEvent.change(role, { target: { value: 'reader' } })
  const dialog = screen.getByRole('alertdialog', { name: 'Change team role' })
  expect(dialog.textContent).toContain('Analyst')
  expect(dialog.textContent).toContain('analyst')
  expect(writes()).toEqual([])
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
  expect((role as HTMLSelectElement).value).toBe('member')
  expect(writes()).toEqual([])
  fireEvent.change(role, { target: { value: 'reader' } })
  fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Change team role' }))
  await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
  expect(writes()).toEqual([['/members/member', 'PATCH', { role: 'reader' }]])
  expect((within(profile).getByRole('textbox', { name: 'Display name' }) as HTMLInputElement).value).toBe('Unsaved profile')
})

it('retains a failed operation in its dialog and never auto-retries it', async () => {
  await open(); failMutation = true
  fireEvent.click(screen.getByRole('button', { name: 'Deactivate' }))
  expect(screen.getByRole('alertdialog').textContent).toContain('sessions and setup links will be revoked')
  fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Deactivate' }))
  await screen.findByText('Access denied')
  expect(screen.getByRole('alertdialog')).toBeTruthy()
  expect(writes()).toEqual([['/members/member', 'PATCH', { active: false }]])
})

it('closes a successful mutation before a failed refresh, so the write is not offered again', async () => {
  await open(); failRefresh = true
  fireEvent.click(screen.getByRole('button', { name: 'Deactivate' }))
  fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Deactivate' }))
  await screen.findByText(/Changes were saved, but the member list could not refresh/)
  expect(screen.queryByRole('alertdialog')).toBeNull()
  expect(writes()).toHaveLength(1)
})

it('creates a reset link only after confirmation and keeps it available without a session refresh', async () => {
  await open()
  fireEvent.click(screen.getByRole('button', { name: 'Reset password' }))
  expect(writes()).toHaveLength(0)
  const before = request.mock.calls.length
  fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Reset password' }))
  const link = await screen.findByRole('textbox', { name: 'One-time account setup link' })
  expect((link as HTMLTextAreaElement).value).toContain('token=fixture')
  expect(request.mock.calls.slice(before)).toEqual([['/members/member/reset', 'POST']])
})

it('offers bounded read recovery without interpreting an outage as a valid signed-out state', async () => {
  failRefresh = true
  render(<LanguageProvider messages={homeMessages}><AccountPage /></LanguageProvider>)
  await screen.findByRole('button', { name: 'Retry' })
  failRefresh = false
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  await screen.findByRole('combobox', { name: 'Team role for Analyst' })
  expect(writes()).toHaveLength(0)
})

it('names the ownership successor before submission and clears the password after success', async () => {
  await open()
  fireEvent.change(screen.getByLabelText('Current password'), { target: { value: 'offline-fixture' } })
  fireEvent.change(screen.getByRole('combobox', { name: 'New team owner' }), { target: { value: 'member' } })
  fireEvent.click(screen.getByRole('button', { name: 'Transfer ownership' }))
  const dialog = screen.getByRole('alertdialog')
  expect(dialog.textContent).toContain('Analyst')
  expect(dialog.textContent).toContain('You will remain an administrator')
  expect(writes()).toHaveLength(0)
  fireEvent.click(within(dialog).getByRole('button', { name: 'Transfer ownership' }))
  await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
  expect(writes()).toEqual([['/team/transfer', 'POST', { user_id: 'member', password: 'offline-fixture' }]])
  expect((screen.getByLabelText('Current password') as HTMLInputElement).value).toBe('')
})
