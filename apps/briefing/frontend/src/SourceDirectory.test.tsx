// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LanguageProvider } from '../../../../packages/ui/src/i18n'
import SourceDirectory from './SourceDirectory'
import type { Source } from './types'
import { countSourceCompleteness } from './sourceCompleteness'

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
  it('reconciles every retained completeness state with its visible label and filter', () => {
    window.history.replaceState(null, '', '/?lang=en')
    const values = ['full_text', 'source_excerpt', 'title_only', 'unavailable', undefined, 'future_state']
    const keys = ['full_text', 'source_excerpt', 'title_only', 'unavailable', 'not_retained', 'unknown']
    const labels = ['Full text', 'Source excerpt', 'Title only', 'Content unavailable', 'Completeness not retained', 'Unrecognized completeness']
    const sources: Source[] = values.map((content_completeness, index) => ({ source_id: `text:${index}`,
      source_type: 'public_document', title: `Material ${index}`, content_completeness }))
    sources.push({ source_id: 'price', source_type: 'numeric' })
    const counts = countSourceCompleteness(sources)
    expect(Object.values(counts).reduce((total, count) => total + count, 0)).toBe(6)
    expect(counts).toEqual(Object.fromEntries(keys.map(key => [key, 1])))
    render(<LanguageProvider enableDomTranslation={false}><SourceDirectory sources={sources}
      timezone="Asia/Shanghai" canReadSources onSource={vi.fn()} /></LanguageProvider>)
    keys.forEach((key, index) => {
      fireEvent.change(screen.getByRole('combobox'), { target: { value: key } })
      expect(screen.getByRole('status').textContent).toBe('1 of 6 materials match')
      expect(screen.getByText(`Material ${index}`).closest('li')?.textContent).toContain(labels[index])
      expect(screen.getAllByRole('button', { name: 'Retained version' })).toHaveLength(1)
    })
    expect(screen.getByText('future_state')).toBeTruthy()
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'Material 2' } })
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'title_only' } })
    expect(screen.getByText('Material 2')).toBeTruthy()
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'not_retained' } })
    expect(screen.getByRole('status').textContent).toBe('0 of 6 materials match')
  })
})
