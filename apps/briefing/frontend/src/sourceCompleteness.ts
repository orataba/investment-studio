import type { Source } from './types'

export const completenessKinds = ['full_text', 'source_excerpt', 'title_only', 'unavailable', 'not_retained', 'unknown'] as const
export type CompletenessKind = typeof completenessKinds[number]
type Copy = (zh: string, en: string) => string

export function sourceCompleteness(value?: string | null): CompletenessKind {
  if (!value) return 'not_retained'
  return ['full_text', 'source_excerpt', 'title_only', 'unavailable'].includes(value) ? value as CompletenessKind : 'unknown'
}

export function completenessLabels(copy: Copy): Record<CompletenessKind, string> {
  return {
    full_text: copy('完整正文', 'Full text'), source_excerpt: copy('来源节选', 'Source excerpt'),
    title_only: copy('仅标题', 'Title only'), unavailable: copy('正文不可用', 'Content unavailable'),
    not_retained: copy('完整性未留存', 'Completeness not retained'), unknown: copy('完整性状态未识别', 'Unrecognized completeness'),
  }
}

export function completenessDescription(value: string | undefined, copy: Copy): string {
  const kind = sourceCompleteness(value)
  return {
    full_text: copy('来源标记为完整正文；此标记不代表已完成事实核验。', 'Marked as full text by the source; this does not establish fact verification.'),
    source_excerpt: copy('来源提供的节选，未包含完整正文。', 'Source excerpt; the full article is not included.'),
    title_only: copy('来源仅保留标题，未提供正文。', 'Only the title was retained; article text was not supplied.'),
    unavailable: copy('来源标记为正文不可用。', 'The source marks article text as unavailable.'),
    not_retained: copy('没有留存正文完整性元数据，不能据此确认正文是否完整。', 'Completeness metadata was not retained; article completeness cannot be confirmed.'),
    unknown: copy(`留存的完整性状态尚未识别：${value}。不能据此确认正文是否完整。`, `Unrecognized retained completeness: ${value}. Article completeness cannot be confirmed.`),
  }[kind]
}

export function countSourceCompleteness(sources: Source[]): Record<CompletenessKind, number> {
  const counts = Object.fromEntries(completenessKinds.map(kind => [kind, 0])) as Record<CompletenessKind, number>
  for (const source of sources) if (source.source_type === 'public_document') counts[sourceCompleteness(source.content_completeness)] += 1
  return counts
}
