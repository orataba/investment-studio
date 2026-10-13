// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LanguageProvider } from '../../../../packages/ui/src/i18n'
import SourceDirectory from './SourceDirectory'
import type { Source } from './types'

describe('retained material directory', () => {
  afterEach(cleanup)
  it('searches the complete retained set across pages and keeps source identity and completeness', () => {
    window.history.replaceState(null, '', '/?lang=en')
    const sources: Source[] = Array.from({ length: 65 }, (_, i) => ({ source_id: `text:${i}`,
      source_type: 'public_document', title: `Article ${i}`, source_name: i === 64 ? 'Unique publisher' : 'Publisher',
      content_completeness: i === 64 ? 'source_excerpt' : 'full_text' }))
    const onSource = vi.fn()
    render(<LanguageProvider enableDomTranslation={false}><SourceDirectory sources={sources}
      timezone="Asia/Shanghai" canReadSources onSource={onSource} /></LanguageProvider>)
    expect(screen.getAllByRole('button', { name: /Retained version|留存版本/ })).toHaveLength(30)
    expect(screen.queryByText('Article 64')).toBeNull()
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'Unique publisher' } })
    expect(screen.getByText('Article 64')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Retained version|留存版本/ }))
    expect(onSource).toHaveBeenCalledWith('text:64')
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'full_text' } })
    expect(screen.queryByText('Article 64')).toBeNull()
    expect(screen.queryByRole('button', { name: /Retained version|留存版本/ })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Clear filters|清除筛选/ }))
    fireEvent.click(screen.getByRole('button', { name: /Next|下一页/ }))
    expect(screen.getByText('Article 30')).toBeTruthy()
    expect(screen.queryByText('Article 0')).toBeNull()
  })
})
