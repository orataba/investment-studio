import { useState } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import ResearchAssistant from '../../../../packages/ui/src/ResearchAssistant'

const topic = (id: string) => ({ topic_id: id, title: `Topic ${id}`, instrument_ids: [], portfolio_id: null, status: 'active', updated_at: '2026-10-02T00:00:00Z' })
const conversation = (id: string) => ({ topic: topic(id), entries: [{ entry_id: `entry-${id}`, kind: 'analysis', title: `Question ${id}`, body: `Answer ${id}`, source: '', context_json: {}, status: 'draft', created_at: '2026-10-02T00:00:00Z' }] })
function setup() {
  let finish!: (value: unknown) => void
  let fail!: (error: Error) => void
  const pending = new Promise((resolve, reject) => { finish = resolve; fail = reject })
  const write = vi.fn(() => pending)
  const upload = vi.fn(() => pending)
  const read = vi.fn(async (path: string) => path === '/research/topics' ? ['A', 'B'].map(topic)
    : path === '/research/catalogue' ? { instruments: [] }
      : path === '/research/connections' ? { assistant_available: true, portfolios: [{ portfolio_id: 'p1', portfolio_name: 'Portfolio 1' }] }
        : conversation(path.split('/').slice(-1)[0]))
  function Harness() {
    const [params, setParams] = useState(new URLSearchParams('topic=A'))
    return <><span data-testid="selected">{params.get('topic')}</span><ResearchAssistant params={params} onParamsChange={setParams}
      read={read as never} write={write as never} uploadTopicFile={upload} canSaveNote={false} attachmentHref={value => value} renderMarkdown={value => value} /></>
  }
  const view = render(<Harness />)
  return { ...view, finish, fail, write, upload }
}
async function switchToB() {
  fireEvent.click(screen.getByRole('button', { name: '历史对话' }))
  fireEvent.click(screen.getByRole('button', { name: /Topic B/ }))
  await screen.findByText('Answer B')
  fireEvent.change(screen.getByLabelText('向研究助手提问'), { target: { value: 'Draft in B' } })
}
function assertB() {
  expect(screen.getByTestId('selected')).toHaveTextContent('B')
  expect(screen.getByText('Answer B')).toBeInTheDocument()
  expect(screen.queryByText('Answer A')).not.toBeInTheDocument()
  expect(screen.getByLabelText('向研究助手提问')).toHaveValue('Draft in B')
  expect(screen.getByLabelText('关联组合')).toHaveValue('')
}

describe('research assistant mutation scope', () => {
  it.each(['send', 'upload', 'association'] as const)('does not apply a late %s result to a newly selected conversation', async operation => {
    const { finish, write, upload } = setup()
    await screen.findByText('Answer A')
    if (operation === 'send') {
      fireEvent.change(screen.getByLabelText('向研究助手提问'), { target: { value: 'Question in A' } })
      fireEvent.click(screen.getByRole('button', { name: '发送' }))
      await waitFor(() => expect(write).toHaveBeenCalled())
    } else if (operation === 'upload') {
      fireEvent.change(screen.getByLabelText('补充对话材料'), { target: { files: [new File(['material'], 'note.txt')] } })
      await waitFor(() => expect(upload).toHaveBeenCalled())
    } else {
      fireEvent.change(screen.getByLabelText('关联组合'), { target: { value: 'p1' } })
      await waitFor(() => expect(write).toHaveBeenCalled())
    }
    await switchToB()
    await act(async () => finish({ ...topic('A'), portfolio_id: 'p1' }))
    assertB()
    fireEvent.click(screen.getByRole('button', { name: '历史对话' }))
    fireEvent.click(screen.getByRole('button', { name: /Topic A/ }))
    await screen.findByText('Answer A')
  })
  it('does not show an old mutation error in a different conversation', async () => {
    const { fail, write } = setup()
    await screen.findByText('Answer A')
    fireEvent.change(screen.getByLabelText('关联组合'), { target: { value: 'p1' } })
    await waitFor(() => expect(write).toHaveBeenCalled())
    await switchToB()
    await act(async () => fail(new Error('Old operation failed')))
    assertB()
    expect(screen.queryByText('Old operation failed')).not.toBeInTheDocument()
  })
  it('preserves a new draft typed while the current conversation sends', async () => {
    const { finish, write } = setup()
    await screen.findByText('Answer A')
    fireEvent.change(screen.getByLabelText('向研究助手提问'), { target: { value: 'First question' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(write).toHaveBeenCalled())
    fireEvent.change(screen.getByLabelText('向研究助手提问'), { target: { value: 'Next question' } })
    await act(async () => finish({}))
    expect(screen.getByLabelText('向研究助手提问')).toHaveValue('Next question')
  })
})
