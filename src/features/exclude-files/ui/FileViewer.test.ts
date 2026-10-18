import { afterEach, describe, expect, it, vi } from 'vitest'
import { createSSRApp, h, ref, type Ref } from 'vue'
import { renderToString } from '@vue/server-renderer'
import type { FileContent } from '@/entities/project/model/types'
import FileViewer from './FileViewer.vue'

const { dependencies } = vi.hoisted(() => ({ dependencies: {} as Record<string, unknown> }))
vi.mock('../model/useFileContent', () => ({ useFileContent: () => dependencies.state }))
vi.mock('@/entities/project', async () => ({
  ...(await import('@/entities/project/lib/filePreview')),
  getSecretLabel: () => 'API token',
}))
vi.mock('@/shared/lib', () => ({ formatBytes: (value: number) => `${value} B` }))
vi.mock('@/shared/ui', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    BaseIcon: defineComponent({ render: () => h('span') }),
    BaseButton: defineComponent({ props: ['disabled'], setup: (props, { attrs, slots }) => () => h('button', { ...attrs, type: 'button', disabled: props.disabled }, slots.default?.()) }),
    CodeBlock: defineComponent({ props: ['code', 'language', 'filename'], setup: (props) => () => h('pre', { 'data-language': props.language, 'data-filename': props.filename }, props.code) }),
    MarkdownContent: defineComponent({ props: ['content'], setup: (props) => () => h('article', { 'data-markdown': 'rendered' }, props.content) }),
  }
})

interface Bindings { mode: Ref<string>; copyStatus: Ref<string | null>; copySource: () => Promise<void> }
async function render(content: FileContent | null, options: { path?: string; source?: boolean; disabled?: boolean; error?: boolean } = {}) {
  const reload = vi.fn()
  dependencies.state = { content: ref(content), isLoading: ref(false), error: ref(options.error ?? false), reload }
  let bindings!: Bindings
  const setup = FileViewer.setup!
  const wrapped = {
    ...FileViewer,
    setup(props: Parameters<typeof setup>[0], context: Parameters<typeof setup>[1]) {
      const result = setup(props, context)
      bindings = result as unknown as Bindings
      if (options.source) bindings.mode.value = 'source'
      return result
    },
  } as typeof FileViewer
  const app = createSSRApp({ render: () => h(wrapped, {
    projectId: 'project', analysisId: 'analysis', file: { path: options.path ?? 'experiment.py', size: 100 }, excluded: false, disabled: options.disabled,
  }) })
  app.config.globalProperties.$t = (key: string) => key
  return { html: await renderToString(app), bindings, reload }
}

afterEach(() => vi.unstubAllGlobals())

describe('rich read-only file preview', () => {
  it.each([['experiment.py', 'python'], ['pyproject.toml', 'toml'], ['environment.yml', 'yaml'], ['data.csv', 'plaintext']])('passes %s source unchanged with language %s', async (path, language) => {
    const code = '  original <script>text</script>\r\n'
    const { html } = await render({ kind: 'text', content: code }, { path })
    expect(html).toContain(`data-language="${language}"`)
    expect(html).toContain(`data-filename="${path}"`)
    expect(html).toContain('  original &lt;script&gt;text&lt;/script&gt;\r\n')
    expect(html).not.toContain('<textarea')
  })

  it('offers rendered Markdown and its exact source without editing controls', async () => {
    const text = '# Experiment\n\n```python\nprint(4)\n```\n'
    const preview = await render({ kind: 'text', content: text }, { path: 'README.md' })
    expect(preview.html).toContain('data-markdown="rendered"')
    expect(preview.html).toContain('analysis.files.source')
    const source = await render({ kind: 'text', content: text }, { path: 'README.md', source: true })
    expect(source.html).toContain('data-language="markdown"')
    expect(source.html).not.toContain('data-markdown="rendered"')
    const writeText = vi.fn(async () => {})
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    await preview.bindings.copySource()
    expect(writeText).toHaveBeenCalledExactlyOnceWith(text)
  })

  it('keeps hidden and binary content out of rich renderers and disables exclusion for readers', async () => {
    for (const kind of ['hidden', 'binary'] as const) {
      const { html } = await render({ kind }, { path: 'config.py', disabled: true })
      expect(html).not.toContain('<pre')
      expect(html).not.toContain('data-markdown')
      expect(html).toContain('disabled')
      expect(html).toContain(kind === 'hidden' ? 'analysis.files.secretHidden' : 'analysis.files.binary')
    }
  })

  it('shows retry for a failed read and preserves the truncated-content warning', async () => {
    const failed = await render(null, { error: true })
    expect(failed.html).toContain('analysis.files.openError')
    expect(failed.html).toContain('common.retry')
    const partial = await render({ kind: 'text', content: 'print(4)\n', truncated: true })
    expect(partial.html).toContain('workflow.truncatedFile')
  })

  it('reports clipboard denial without changing the displayed Markdown source', async () => {
    const text = '# Original text\n'
    const preview = await render({ kind: 'text', content: text }, { path: 'README.md' })
    vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn(async () => { throw new Error('Denied') }) } })
    await preview.bindings.copySource()
    expect(preview.bindings.copyStatus.value).toBe('error')
    expect(preview.html).toContain(text)
  })
})
