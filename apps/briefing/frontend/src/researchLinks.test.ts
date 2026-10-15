import { describe, expect, it } from 'vitest'
import remarkResearchLinks, { researchLinkTarget } from '../../../../packages/ui/src/remarkResearchLinks'

describe('research links in Chinese prose', () => {
  const url = 'https://wallstreetcn.com/articles/3756789'
  it.each(['。', '；2026-10-14', '，随后变化', '（2026-10-14）', '）'])('leaves %s outside an inferred link', suffix => {
    expect(researchLinkTarget(url + suffix)).toEqual({ url, suffix })
  })
  it('preserves legitimate query, hash, encoded punctuation and balanced path parentheses', () => {
    for (const queryUrl of [url + '?q=earnings&date=2026-10-14#results', url + '?q=%E5%88%A9%E6%B6%A6%EF%BC%8C%E5%A2%9E%E9%95%BF', url + '/(full_report)?q=a;b']) {
      expect(researchLinkTarget(queryUrl)).toEqual({ url: queryUrl, suffix: '' })
    }
  })
  it('prefers exact retained URLs, including Chinese punctuation in valid query values', () => {
    const retained = url + '?q=利润，增长'
    expect(researchLinkTarget(retained, [retained])).toEqual({ url: new URL(retained).href, suffix: '' })
    expect(researchLinkTarget(retained + '。2026-10-14', [retained])).toEqual({ url: new URL(retained).href, suffix: '。2026-10-14' })
    expect(researchLinkTarget(url + '%E3%80%82', [url], false)).toEqual({ url, suffix: '%E3%80%82' })
    expect(researchLinkTarget(url + '?q=full', [url])).toEqual({ url: url + '?q=full', suffix: '' })
  })
  it('does not rewrite explicit unverified destinations or private protocols', () => {
    expect(researchLinkTarget(url + '/中文，路径', [], false).url).toBe(url + '/中文，路径')
    expect(researchLinkTarget('javascript:alert(1)')).toEqual({ url: 'javascript:alert(1)', suffix: '' })
  })
  it('splits only inferred link text and keeps adjacent content and explicit link labels', () => {
    const tree = { type: 'root', children: [{ type: 'paragraph', children: [
      { type: 'link', url: url + '%E3%80%82', children: [{ type: 'text', value: url + '。' }] },
      { type: 'text', value: '下一句' },
      { type: 'link', url: url + '%E3%80%82', children: [{ type: 'text', value: '留存文章' }] },
    ] }] }
    remarkResearchLinks({ sourceUrls: [url] })(tree)
    expect(tree.children[0].children.map(node => node.type)).toEqual(['link', 'text', 'text', 'link'])
    expect(tree.children[0].children[0].url).toBe(url)
    expect(tree.children[0].children[1].value).toBe('。')
    expect(tree.children[0].children[3].url).toBe(url)
    expect(tree.children[0].children[3].children?.[0].value).toBe('留存文章')
  })
})
