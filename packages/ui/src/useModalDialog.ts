import { useEffect, useRef, type RefObject } from 'react'

import { ModalStack } from './modalStack'

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

type ElementRef = { current: HTMLElement | null }

const modalStack = new ModalStack()

export function useModalDialog(
  open: boolean,
  onClose: () => void,
  initialFocusRef?: ElementRef,
): RefObject<HTMLDivElement | null> {
  const dialogRef = useRef<HTMLDivElement>(null)
  const onCloseRef = useRef(onClose)
  const modalTokenRef = useRef(Symbol('investment-studio-modal'))

  useEffect(() => {
    onCloseRef.current = onClose
  }, [onClose])

  useEffect(() => {
    if (!open) {
      return undefined
    }

    const modalToken = modalTokenRef.current
    modalStack.register(modalToken)
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const focusDialog = window.requestAnimationFrame(() => {
      if (!modalStack.isTopmost(modalToken)) {
        return
      }
      const dialog = dialogRef.current
      if (dialog?.contains(document.activeElement)) {
        return
      }
      const target = initialFocusRef?.current ?? dialog?.querySelector<HTMLElement>(FOCUSABLE_SELECTOR) ?? dialog
      target?.focus()
    })

    function handleKeyDown(event: KeyboardEvent) {
      if (!modalStack.isTopmost(modalToken)) {
        return
      }
      const dialog = dialogRef.current
      if (!dialog) {
        return
      }
      if (event.key === 'Escape') {
        event.preventDefault()
        onCloseRef.current()
        return
      }
      if (event.key !== 'Tab') {
        return
      }

      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
        (element) => element.getAttribute('aria-hidden') !== 'true',
      )
      if (!focusable.length) {
        event.preventDefault()
        dialog.focus()
        return
      }

      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (!dialog.contains(document.activeElement)) {
        event.preventDefault()
        ;(event.shiftKey ? last : first).focus()
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      window.cancelAnimationFrame(focusDialog)
      document.removeEventListener('keydown', handleKeyDown)
      const wasTopmost = modalStack.unregister(modalToken)
      if (wasTopmost && previouslyFocused?.isConnected) {
        window.requestAnimationFrame(() => {
          if (previouslyFocused.isConnected) {
            previouslyFocused.focus()
          }
        })
      }
    }
  }, [initialFocusRef, open])

  return dialogRef
}
