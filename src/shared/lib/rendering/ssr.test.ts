import { describe, expect, it } from 'vitest'
import { createSSRApp, h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { CodeBlock, MarkdownContent } from '@/shared/ui'

describe('shared rendering without browser globals', () => {
  it('imports the real shared/i18n modules and renders code on the server', async () => {
    expect(typeof document).toBe('undefined')
    const html = await renderToString(
      createSSRApp({
        render: () =>
          h(CodeBlock, { code: 'import numpy', language: 'python', lineNumbers: false }),
      }),
    )
    expect(html).toContain('hljs-keyword')
    expect(html).not.toContain('code-block__numbers')
  })

  it('keeps SSR Markdown HTML-disabled and rejects active links without a DOM sanitizer', async () => {
    const html = await renderToString(
      createSSRApp({
        render: () =>
          h(MarkdownContent, {
            content:
              '# README\n\n<script>alert(1)</script>\n\n[x](javascript:alert(1))\n\n![tracker](https://tracker.example/pixel)',
          }),
      }),
    )
    expect(html).toContain('<h1>README</h1>')
    expect(html).not.toContain('<script>')
    expect(html).not.toContain('<img')
    expect(html).not.toContain('href="javascript:')
  })
})
