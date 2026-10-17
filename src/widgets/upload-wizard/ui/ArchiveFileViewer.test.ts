import { afterEach, describe, expect, it, vi } from 'vitest'
import { createSSRApp, h, reactive, type Ref } from 'vue'
import { renderToString } from '@vue/server-renderer'
import type { FileContent } from '@/entities/project/model/types'
import ArchiveFileViewer from './ArchiveFileViewer.vue'

const { dependencies } = vi.hoisted(() => ({ dependencies: {} as Record<string, unknown> }))
vi.mock('../model/archiveFilePreview', () => ({ useArchiveFilePreview: () => dependencies.preview }))
vi.mock('@/entities/project', async () => ({ ...(await import('@/entities/project/lib/filePreview')) }))
vi.mock('@/shared/i18n', () => ({ t: (key: string) => key }))
vi.mock('@/shared/ui', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    BaseIcon: defineComponent({ render: () => null }),
    BaseDialog: defineComponent({ props: ['open', 'size', 'title'], setup: (props, { slots }) => () => props.open ? h('section', { role: 'dialog', 'data-size': props.size }, [slots.default?.(), slots.actions?.()]) : null }),
    BaseButton: defineComponent({ props: ['disabled'], setup: (props, { attrs, slots }) => () => h('button', { ...attrs, type: 'button', disabled: props.disabled }, slots.default?.()) }),
    CodeBlock: defineComponent({ props: ['code', 'language', 'filename'], setup: (props) => () => h('pre', { 'data-language': props.language, 'data-filename': props.filename }, props.code) }),
    MarkdownContent: (await import('@/shared/ui/MarkdownContent.vue')).default,
  }
})

interface Bindings { rendered: Ref<boolean>; copyStatus: Ref<string | null>; copySource: () => Promise<void> }
async function render(content: FileContent | null, options: { path?: string; source?: boolean; error?: string; needsRefresh?: boolean } = {}) {
  const preview = { state: reactive({ content, isLoading: false, error: options.error ?? null, needsRefresh: options.needsRefresh ?? false }), retry: vi.fn() }
  dependencies.preview = preview
  let bindings!: Bindings
  const setup = ArchiveFileViewer.setup!
  const wrapped = {
    ...ArchiveFileViewer,
    setup(props: Parameters<typeof setup>[0], context: Parameters<typeof setup>[1]) {
      const result = setup(props, context)
      bindings = result as unknown as Bindings
      if (options.source) bindings.rendered.value = false
      return result
    },
  } as typeof ArchiveFileViewer
  const app = createSSRApp({ render: () => h(wrapped, {
    open: true, projectId: 'project', archiveTag: 'a'.repeat(64), path: options.path ?? 'README.md',
  }) })
  app.config.globalProperties.$t = (key: string) => key
  return { html: await renderToString(app), bindings, preview }
}

afterEach(() => vi.unstubAllGlobals())

describe('built archive file dialog', () => {
  it('renders actual README Markdown with headings, lists and tables while retaining a source view', async () => {
    const content = '# Experiment\n\n- Prepare input\n- Run locally\n\n| Input | Output |\n| --- | --- |\n| csv | json |\n\n<script>bad()</script>\n'
    const { html } = await render({ kind: 'text', content })
    expect(html).toContain('<h1>Experiment</h1>')
    expect(html).toContain('<ul>')
    expect(html).toContain('<table>')
    expect(html).not.toContain('<script>')
    expect(html).toContain('data-size="wide"')
    expect(html).toContain('analysis.files.source')
    expect(html).toContain('common.close')
    expect(html).not.toContain('<textarea')
    expect(html).not.toContain('analysis.files.exclude')
  })

  it.each([['project/experiment.py', 'python'], ['analysis-report.json', 'json'], ['Dockerfile', 'dockerfile'], ['requirements.txt', 'plaintext']])('passes exact %s contents to the %s code renderer', async (path, language) => {
    const content = '  Original <text>\r\n'
    const { html } = await render({ kind: 'text', content }, { path })
    expect(html).toContain(`data-language="${language}"`)
    expect(html).toContain(`data-filename="${path}"`)
    expect(html).toContain('  Original &lt;text&gt;\r\n')
  })

  it('shows and copies the exact Markdown source without converting it to rendered HTML', async () => {
    const content = '# Original\r\n\r\n```python\r\nprint(4)\r\n```\r\n'
    const source = await render({ kind: 'text', content }, { source: true })
    expect(source.html).toContain('data-language="markdown"')
    expect(source.html).not.toContain('<h1>Original</h1>')
    const writeText = vi.fn(async () => {})
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    await source.bindings.copySource()
    expect(writeText).toHaveBeenCalledExactlyOnceWith(content)
  })

  it.each(['hidden', 'binary'] as const)('does not pass %s content to Markdown or code rendering', async (kind) => {
    const { html } = await render({ kind })
    expect(html).not.toContain('<pre')
    expect(html).not.toContain('markdown-content')
    expect(html).toContain(kind === 'hidden' ? 'ready.files.hidden' : 'analysis.files.binary')
  })

  it('distinguishes transient retry from a stale archive that needs current metadata', async () => {
    const temporary = await render(null, { error: 'ready.files.error' })
    expect(temporary.html).toContain('common.retry')
    expect(temporary.html).not.toContain('ready.files.refresh')
    const stale = await render(null, { error: 'ready.files.stale', needsRefresh: true })
    expect(stale.html).toContain('ready.files.refresh')
    expect(stale.html).not.toContain('common.retry')
  })

  it('labels a truncated response instead of presenting it as the complete file', async () => {
    const { html } = await render({ kind: 'text', content: 'partial', truncated: true }, { path: 'project/large.txt' })
    expect(html).toContain('workflow.truncatedFile')
    expect(html).toContain('partial')
  })
})
