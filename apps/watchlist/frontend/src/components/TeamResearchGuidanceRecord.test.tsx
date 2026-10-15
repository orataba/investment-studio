// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import TeamResearchGuidanceRecord from './TeamResearchGuidanceRecord'
import type { TeamResearchGuidance } from '../lib/researchDossierApi'
const api = vi.hoisted(() => ({ read: vi.fn(), save: vi.fn(), role: 'member' }))
vi.mock('../lib/researchDossierApi', () => ({ getTeamResearchGuidance: api.read, saveTeamResearchGuidance: api.save }))
vi.mock('./AccountBoundary', () => ({ useStudioAccount: () => ({ team_role: api.role }) }))
const guidance: TeamResearchGuidance = { team_id: 'team', guidance: '核实长期现金回报。', section_skills: { overview: '综合判断', recent_updates: '先比较既有跟进', themes: '按重要主题拆分', quantitative: '使用可比数值', risk: '独立评估风险', review: '独立核对证据' }, revision: 4, author: null, updated_at: null, versions: [] }
beforeEach(() => { vi.resetAllMocks(); api.role = 'member'; api.read.mockResolvedValue(guidance) })
afterEach(cleanup)

it('saves all six roles under the original revision without starting research', async () => {
  api.save.mockImplementation(async input => ({ ...guidance, ...input, revision: 5 }))
  render(<TeamResearchGuidanceRecord />)
  fireEvent.click(await screen.findByRole('button', { name: '编辑团队指导' }))
  fireEvent.change(screen.getByLabelText('主题专员 · 重要研究主题'), { target: { value: '只拆分相互独立的主题。' } })
  fireEvent.click(screen.getByRole('button', { name: '保存团队指导' }))
  await waitFor(() => expect(api.save).toHaveBeenCalledWith({ guidance: guidance.guidance, expected_revision: 4, section_skills: { ...guidance.section_skills, themes: '只拆分相互独立的主题。' } }))
  await screen.findByRole('button', { name: '编辑团队指导' })
  expect(api.read).toHaveBeenCalledTimes(1)
})

it('preserves the draft on a conflict and only uses the newer revision after explicit reconciliation', async () => {
  api.save.mockRejectedValueOnce(new Error('研究指导已由其他成员更新'))
  render(<TeamResearchGuidanceRecord />)
  fireEvent.click(await screen.findByRole('button', { name: '编辑团队指导' }))
  fireEvent.change(screen.getByLabelText('团队共同研究指导'), { target: { value: '我的未保存指导' } })
  fireEvent.click(screen.getByRole('button', { name: '保存团队指导' }))
  await screen.findByRole('alert')
  expect(screen.getByLabelText('团队共同研究指导')).toHaveProperty('value', '我的未保存指导')
  api.read.mockResolvedValue({ ...guidance, guidance: '另一位成员的新指导', revision: 5 })
  fireEvent.click(screen.getByRole('button', { name: '读取最新指导并核对' }))
  await screen.findByText('另一位成员的新指导')
  expect(screen.getByLabelText('团队共同研究指导')).toHaveProperty('value', '我的未保存指导')
  api.save.mockResolvedValue({ ...guidance, guidance: '我的未保存指导', revision: 6 })
  fireEvent.click(screen.getByRole('button', { name: '保存团队指导' }))
  await waitFor(() => expect(api.save).toHaveBeenLastCalledWith(expect.objectContaining({ guidance: '我的未保存指导', expected_revision: 5 })))
})

it('requires an audit reason only when changing the risk role and submits it with the revision', async () => {
  api.save.mockImplementation(async input => ({ ...guidance, ...input, revision: 5 }))
  render(<TeamResearchGuidanceRecord />)
  fireEvent.click(await screen.findByRole('button', { name: '编辑团队指导' }))
  expect(screen.queryByLabelText('风控指导修改原因')).toBeNull()
  fireEvent.change(screen.getByLabelText('风控专员'), { target: { value: '只保留重要风险。' } })
  const reason = screen.getByLabelText('风控指导修改原因')
  const save = screen.getByRole('button', { name: '保存团队指导' })
  expect(reason).toHaveProperty('required', true)
  expect(save).toHaveProperty('disabled', true)
  fireEvent.change(reason, { target: { value: '   ' } })
  fireEvent.click(save)
  expect(api.save).not.toHaveBeenCalled()
  fireEvent.change(reason, { target: { value: '  明确重要性筛选  ' } })
  fireEvent.click(save)
  await waitFor(() => expect(api.save).toHaveBeenCalledWith({ guidance: guidance.guidance,
    expected_revision: 4, section_skills: { ...guidance.section_skills, risk: '只保留重要风险。' }, reason: '明确重要性筛选' }))
  await screen.findByRole('button', { name: '编辑团队指导' })
})

it('allows readers to inspect the six saved roles without exposing edit actions', async () => {
  api.role = 'reader'
  render(<TeamResearchGuidanceRecord />)
  await screen.findByText('核实长期现金回报。')
  expect(screen.getByText('独立核对证据')).toBeTruthy()
  expect(screen.queryByRole('button', { name: '编辑团队指导' })).toBeNull()
  await act(async () => {})
  expect(api.save).not.toHaveBeenCalled()
})

it('returns focus to the edit control after cancel removes the form', async () => {
  render(<TeamResearchGuidanceRecord />)
  fireEvent.click(await screen.findByRole('button', { name: '编辑团队指导' }))
  const cancel = screen.getByRole('button', { name: '取消' })
  cancel.focus()
  fireEvent.click(cancel)
  expect(document.activeElement).toBe(screen.getByRole('button', { name: '编辑团队指导' }))
  expect(api.save).not.toHaveBeenCalled()
})
