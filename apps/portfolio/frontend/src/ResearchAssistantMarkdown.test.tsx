import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkResearchLinks from '../../../../packages/ui/src/remarkResearchLinks'

it('renders retained sources and Chinese punctuation safely through the actual Markdown and GFM pipeline', () => {
  const url = 'https://wallstreetcn.com/articles/3783377'
  render(<Markdown remarkPlugins={[remarkGfm, [remarkResearchLinks, { sourceUrls: [url] }]]}>
    {`[规范文章](${url}) / ${url}。 / ${url}；2026年10月15日`}
  </Markdown>)
  expect(screen.getAllByRole('link')).toHaveLength(3)
  for (const link of screen.getAllByRole('link')) expect(link).toHaveAttribute('href', url)
  expect(document.body).toHaveTextContent('。 /')
  expect(document.body).toHaveTextContent('；2026年10月15日')
})
