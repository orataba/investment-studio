import { lazy, Suspense, useState, type ComponentType } from 'react'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { expect, it } from 'vitest'
import { LanguageProvider, LanguageSelector } from '../../../../packages/ui/src/i18n'
import WorkspaceLoadingDrawer from '../../../../packages/ui/src/WorkspaceLoadingDrawer'
import { useModalDialog } from '../../../../packages/ui/src/useModalDialog'

type DrawerProps = { onClose: () => void }
function ReadyDrawer({ onClose }: DrawerProps) {
  const ref = useModalDialog(true, onClose)
  return <div ref={ref} role="dialog" aria-modal="true" aria-label="Loaded drawer"><button onClick={onClose}>Close loaded drawer</button></div>
}
function fixture(language: 'en' | 'zh-Hans' = 'en') {
  window.history.replaceState(null, '', '/?lang=' + language)
  let resolve!: (module: { default: ComponentType<DrawerProps> }) => void
  const ready = new Promise<{ default: ComponentType<DrawerProps> }>(done => { resolve = done })
  const Drawer = lazy(() => ready)
  function Workspace() {
    const [open, setOpen] = useState(false)
    const close = () => setOpen(false)
    return <LanguageProvider enableDomTranslation={false}><LanguageSelector /><header><button onClick={() => setOpen(true)}>Open assistant</button></header>
      {open && <Suspense fallback={<WorkspaceLoadingDrawer kind="assistant" onClose={close} />}><Drawer onClose={close} /></Suspense>}
    </LanguageProvider>
  }
  render(<Workspace />)
  screen.getByRole('button', { name: 'Open assistant' }).focus()
  fireEvent.click(screen.getByRole('button', { name: 'Open assistant' }))
  return () => resolve({ default: ReadyDrawer })
}

it('keeps pending drawer loading inside the dismissible drawer and restores the trigger on Escape', async () => {
  fixture()
  const drawer = screen.getByRole('dialog', { name: 'Research assistant · Loading' })
  expect(within(drawer).getByRole('status')).toHaveTextContent('Loading')
  expect(drawer.parentElement).toHaveClass('assistant-backdrop')
  await waitFor(() => expect(within(drawer).getByRole('button', { name: 'Close Panel' })).toHaveFocus())
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(document.querySelector('[aria-modal="true"]')).toBeNull()
  await waitFor(() => expect(screen.getByRole('button', { name: 'Open assistant' })).toHaveFocus())
})

it('hands focus to the real drawer when its code becomes available and leaves no pending modal behind', async () => {
  const resolve = fixture()
  await waitFor(() => expect(screen.getByRole('button', { name: 'Close Panel' })).toHaveFocus())
  await act(async () => resolve())
  const drawer = await screen.findByRole('dialog', { name: 'Loaded drawer' })
  expect(screen.getAllByRole('dialog')).toHaveLength(1)
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  await waitFor(() => expect(within(drawer).getByRole('button', { name: 'Close loaded drawer' })).toHaveFocus())
  fireEvent.click(within(drawer).getByRole('button', { name: 'Close loaded drawer' }))
  expect(document.querySelector('[aria-modal="true"]')).toBeNull()
  await waitFor(() => expect(screen.getByRole('button', { name: 'Open assistant' })).toHaveFocus())
})


it('uses a close action instead of the market close label in both languages', async () => {
  fixture('zh-Hans')
  const close = screen.getByRole('button', { name: '关闭面板' })
  expect(screen.queryByRole('button', { name: '收盘价' })).not.toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('语言'), { target: { value: 'en' } })
  expect(screen.getByRole('button', { name: 'Close Panel' })).toBe(close)
  fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'zh-Hans' } })
  fireEvent.click(screen.getByRole('button', { name: '关闭面板' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})
