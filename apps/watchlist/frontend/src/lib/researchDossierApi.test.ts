import { beforeEach, expect, it, vi } from 'vitest'
import { fetchJson } from './api'
import { getResearchTheme, getResearchThemes } from './researchDossierApi'

vi.mock('./api', () => ({ fetchJson: vi.fn() }))

beforeEach(() => {
  vi.mocked(fetchJson).mockReset().mockResolvedValue({ themes: [] })
})

it('uses current theme cards by default for the opinion timeline and theme selector', async () => {
  const signal = new AbortController().signal
  await getResearchThemes('xlk', signal)
  expect(fetchJson).toHaveBeenCalledWith('/api/research/instruments/xlk/themes?include_history=false', { signal })
})

it('keeps explicit historical lists and individual theme originals available', async () => {
  await getResearchThemes('xlk', undefined, true)
  expect(fetchJson).toHaveBeenLastCalledWith('/api/research/instruments/xlk/themes', { signal: undefined })
  await getResearchTheme('xlk', 'saved-theme')
  expect(fetchJson).toHaveBeenLastCalledWith('/api/research/instruments/xlk/themes/saved-theme', { signal: undefined })
})
