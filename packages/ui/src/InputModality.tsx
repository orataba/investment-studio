import { useEffect } from 'react'

// Native selects can retain :focus-visible after a pointer selection.
// Track the last input method so mouse focus never looks like a selected state.
export default function InputModality() {
  useEffect(() => {
    const root = document.documentElement
    const pointer = () => { root.dataset.inputModality = 'pointer' }
    const keyboard = () => { root.dataset.inputModality = 'keyboard' }
    document.addEventListener('pointerdown', pointer, true)
    document.addEventListener('keydown', keyboard, true)
    return () => {
      document.removeEventListener('pointerdown', pointer, true)
      document.removeEventListener('keydown', keyboard, true)
      delete root.dataset.inputModality
    }
  }, [])
  return null
}
