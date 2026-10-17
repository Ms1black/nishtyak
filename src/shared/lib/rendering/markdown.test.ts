// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { MAX_MARKDOWN_CHARACTERS, renderMarkdown, safeMarkdownLink } from './markdown'

function documentFor(source: string) {
  const container = document.createElement('div')
  container.innerHTML = renderMarkdown(source).html
  return container
}

describe('safe project Markdown', () => {
  it('renders headings, lists, inline code, tables and explicitly fenced source', () => {
    const element = documentFor(
      '# Experiment\n\nUse `numpy`.\n\n1. Install\n2. Run\n\n| File | Role |\n| --- | --- |\n| data.csv | Input |\n\n```python\nimport numpy as np\nprint(np.mean([1, 2]))\n```',
    )
    expect(element.querySelector('h1')?.textContent).toBe('Experiment')
    expect(element.querySelectorAll('ol li')).toHaveLength(2)
    expect(element.querySelector('p code')?.textContent).toBe('numpy')
    expect(element.querySelector('table tbody td')?.textContent).toBe('data.csv')
    expect(element.querySelector('pre .hljs-keyword')?.textContent).toBe('import')
  })

  it.each([
    '<script>alert(document.cookie)</script>',
    '<img src="https://tracker.example/pixel" onerror="alert(1)">',
    '<svg><a xlink:href="javascript:alert(1)">x</a></svg>',
    '<iframe srcdoc="<script>alert(1)</script>"></iframe>',
    '<style>body{background:url(https://tracker.example)}</style>',
    '<form action="/api/delete"><input autofocus onfocus="alert(1)"></form>',
  ])('never turns raw HTML into active project content: %s', (source) => {
    const element = documentFor(source)
    expect(element.querySelector('script,img,svg,iframe,style,form,input,object,embed')).toBeNull()
    expect(element.querySelector('[onerror],[onload],[onfocus],[style],[src]')).toBeNull()
    expect(element.textContent).toContain('<')
  })

  it.each([
    'javascript:alert(1)',
    'JaVaScRiPt:alert(1)',
    'data:text/html,hi',
    'data:image/svg+xml,hi',
    'vbscript:msgbox(1)',
    'file:///etc/passwd',
    '//tracker.example/pixel',
    'https://user:secret@example.com',
  ])('rejects unsafe link protocol or authority: %s', (url) => {
    expect(safeMarkdownLink(url)).toBe(false)
    const element = documentFor(`[link](${url})`)
    expect(element.querySelector('a')).toBeNull()
  })

  it('blocks encoded scheme attacks and image tracking even through valid Markdown syntax', () => {
    const element = documentFor(
      '[bad](java&#x73;cript:alert(1))\n\n![picture](https://tracker.example/pixel.png)\n\n![inline](data:image/png;base64,aGVsbG8=)',
    )
    expect(element.querySelector('a,img,picture,source,svg')).toBeNull()
    expect(element.textContent).toContain('picture')
  })

  it('keeps user-activated HTTPS links isolated and does not create clobberable ids', () => {
    const element = documentFor(
      '# constructor\n\n[Docs](https://example.com/docs)\n\n[Local](README.md)',
    )
    const links = element.querySelectorAll('a')
    expect(links).toHaveLength(2)
    expect(links[0]?.getAttribute('target')).toBe('_blank')
    expect(links[0]?.getAttribute('rel')).toContain('noopener')
    expect(links[0]?.getAttribute('rel')).toContain('noreferrer')
    expect(element.querySelector('[id],[name]')).toBeNull()
    expect(links[1]?.getAttribute('href')).toBe('README.md')
  })

  it('retains ordinary relative document links and fragments after sanitization', () => {
    const element = documentFor('[Data](./data.csv) [Root](/docs) [Section](#install)')
    expect([...element.querySelectorAll('a')].map((link) => link.getAttribute('href'))).toEqual([
      './data.csv',
      '/docs',
      '#install',
    ])
  })

  it('escapes hostile code fences and inline source independently of Markdown parsing', () => {
    const element = documentFor(
      '```python\nprint("<img src=x onerror=alert(1)>")\n```\n\n`<svg onload=alert(1)>`',
    )
    expect(element.querySelector('img,svg')).toBeNull()
    expect(element.querySelector('pre code')?.textContent).toContain('<img')
    expect(element.querySelector('p code')?.textContent).toContain('<svg')
  })

  it('bounds large Markdown input before parsing', () => {
    const result = renderMarkdown('x'.repeat(MAX_MARKDOWN_CHARACTERS + 100))
    expect(result.truncated).toBe(true)
    expect(result.html.length).toBeLessThan(MAX_MARKDOWN_CHARACTERS + 100)
  })
})
