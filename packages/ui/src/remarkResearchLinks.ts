type MarkdownNode = {
  type: string
  url?: string
  value?: string
  children?: MarkdownNode[]
  position?: { start: { offset?: number }; end: { offset?: number } }
}

const proseBoundary = /[。；，、！？：。「」『』【】《》〈〉（）“”‘’]/u
const publicUrl = (value: string) => {
  try { const url = new URL(value); return /^https?:$/.test(url.protocol) ? url.href : undefined } catch { return undefined }
}

/** Only a matching retained URL may override an explicit Markdown destination. */
export function researchLinkTarget(value: string, sourceUrls: readonly string[] = [], bare = true) {
  const original = publicUrl(value)
  if (!original) return { url: value, suffix: '' }
  const retained = sourceUrls.map(url => ({ text: url, url: publicUrl(url) })).filter(source => source.url)
    .sort((left, right) => right.text.length - left.text.length)
  if (retained.some(source => source.url === original)) return { url: original, suffix: '' }
  // Check a literal or encoded prose boundary without decoding legitimate URL
  // path/query escapes. Encoded Chinese text may be part of a valid URL.
  for (let index = 0; index < value.length; index += 1) {
    const suffix = value.slice(index)
    const punctuation = proseBoundary.test(suffix[0]) || /^[)\]};,.](?:\s|$|\d{4}-\d{2}-\d{2})/.test(suffix)
    if (!punctuation && !/^%(?:E3%80%82|EF%BC%9B|EF%BC%8C|EF%BC%89)/i.test(suffix)) continue
    const candidate = publicUrl(value.slice(0, index))
    const source = retained.find(item => item.url === candidate)
    if (source) return { url: source.url!, suffix }
  }
  if (!bare) return { url: value, suffix: '' }
  const boundary = value.search(proseBoundary)
  if (boundary < 0) return { url: value, suffix: '' }
  const url = publicUrl(value.slice(0, boundary))
  return url ? { url, suffix: value.slice(boundary) } : { url: value, suffix: '' }
}

/** Run after GFM: split Chinese sentence punctuation out of inferred links. */
export default function remarkResearchLinks({ sourceUrls = [] }: { sourceUrls?: readonly string[] } = {}) {
  return (tree: MarkdownNode, file?: { value?: unknown }) => {
    const markdown = typeof file?.value === 'string' ? file.value : ''
    function visit(parent: MarkdownNode) {
      if (!parent.children) return
      for (let index = 0; index < parent.children.length; index += 1) {
        const node = parent.children[index]
        if (node.type !== 'link' || !node.url) { visit(node); continue }
        const text = node.children?.length === 1 && node.children[0].type === 'text' ? node.children[0].value : undefined
        const start = node.position?.start.offset
        const raw = start === undefined ? '' : markdown.slice(start, node.position?.end.offset)
        const bare = Boolean(text?.match(/^https?:\/\//i) && (!raw || /^https?:\/\//i.test(raw)))
        const target = researchLinkTarget(bare ? text! : node.url, sourceUrls, bare)
        node.url = target.url
        if (bare && target.suffix) {
          node.children = [{ type: 'text', value: text!.slice(0, -target.suffix.length) }]
          parent.children.splice(index + 1, 0, { type: 'text', value: target.suffix })
          index += 1
        }
      }
    }
    visit(tree)
  }
}
