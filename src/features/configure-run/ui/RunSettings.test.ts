import { describe, expect, it, vi } from 'vitest'
import { createSSRApp, h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import RunSettings from './RunSettings.vue'

vi.mock('@/entities/project', async () => ({ formatRunCommand: (await import('@/entities/project/lib/decisions')).formatRunCommand }))
vi.mock('@/shared/i18n', () => ({ t: (key: string) => key }))
vi.mock('@/shared/ui', async () => {
  const { defineComponent, h } = await import('vue')
  const field = (tag: string) => defineComponent({ render: () => h(tag) })
  return {
    BaseInput: field('input'), BaseTextarea: field('textarea'), BaseSelect: field('select'), SegmentedControl: field('div'),
    InlineCode: defineComponent({ props: ['code', 'language'], setup: (props) => () => h('code', { 'data-language': props.language }, props.code) }),
    CodeBlock: defineComponent({ props: ['code', 'language', 'lineNumbers'], setup: (props) => () => h('section', { 'data-codeblock': props.language, 'data-line-numbers': String(props.lineNumbers) }, [h('pre', props.code), h('button', { type: 'button', 'data-copy': '' }, 'Copy')]) }),
  }
})

describe('launch command rendering', () => {
  it('uses a copyable shell block for the current command and noninteractive shell syntax inside candidates', async () => {
    const runSpec = { kind: 'script' as const, target: 'experiment.py', args: ['--input', 'my data.csv'], workingDirectory: '.' }
    const app = createSSRApp({ render: () => h(RunSettings, {
      runSpec, pythonVersion: '3.11', entrypoints: ['experiment.py'], scriptFiles: ['experiment.py'], pythonRequires: null,
      candidates: [{ id: 'candidate', runSpec, reason: 'Detected main guard', confidence: 'high' }],
    }) })
    app.config.globalProperties.$t = (key: string) => key
    const html = await renderToString(app)
    expect(html).toContain('data-codeblock="bash" data-line-numbers="false"')
    const candidate = html.match(/<button[^>]*class="run__candidate"[^>]*>([\s\S]*?)<\/button>/)?.[1]
    expect(candidate).toContain('data-language="bash"')
    expect(candidate).toContain('python experiment.py --input')
    expect(candidate).not.toContain('<button')
    expect(candidate).not.toContain('data-copy')
    expect(html.match(/data-copy/g)).toHaveLength(1)
  })
})
