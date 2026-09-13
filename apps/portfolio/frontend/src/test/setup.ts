import '@testing-library/jest-dom/vitest'
import { afterEach, beforeEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'
import { LANGUAGE_COOKIE_NAME, LANGUAGE_STORAGE_KEY } from '../../../../../packages/ui/src/i18n'

function createStorageStub(): Storage {
  const values = new Map<string, string>()
  return {
    get length() {
      return values.size
    },
    clear() {
      values.clear()
    },
    getItem(key: string) {
      return values.get(key) ?? null
    },
    key(index: number) {
      return [...values.keys()][index] ?? null
    },
    removeItem(key: string) {
      values.delete(key)
    },
    setItem(key: string, value: string) {
      values.set(key, String(value))
    },
  }
}

Object.defineProperty(window, 'localStorage', {
  configurable: true,
  value: createStorageStub(),
})

Object.defineProperty(window, 'sessionStorage', {
  configurable: true,
  value: createStorageStub(),
})

function resetLanguagePreference() {
  const url = new URL(window.location.href)
  url.searchParams.delete('lang')
  url.searchParams.delete('language')
  window.history.replaceState(null, '', url)
  document.cookie = `${LANGUAGE_COOKIE_NAME}=; path=/; max-age=0`
  document.documentElement.lang = 'en'
  document.documentElement.dataset.language = 'en'
}

beforeEach(() => {
  resetLanguagePreference()
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, 'en')
  document.cookie = `${LANGUAGE_COOKIE_NAME}=en; path=/; samesite=lax`
})

afterEach(() => {
  cleanup()
  window.localStorage.clear()
  window.sessionStorage.clear()
  resetLanguagePreference()
})

Object.defineProperty(window, 'matchMedia', {
  configurable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

Object.defineProperty(window, 'ResizeObserver', {
  configurable: true,
  value: ResizeObserverStub,
})

Object.defineProperty(window, 'scrollTo', {
  configurable: true,
  value: vi.fn(),
})
