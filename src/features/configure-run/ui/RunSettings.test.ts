import { describe, expect, it, vi } from 'vitest'
import { createSSRApp, h, type Ref } from 'vue'
import { renderToString } from '@vue/server-renderer'
import RunSettings from './RunSettings.vue'
import type { LaunchCandidate, RunKind, RunSpec } from '@/entities/project/model/types'

vi.mock('@/entities/project', async () => ({ formatRunCommand: (await import('@/entities/project/lib/decisions')).formatRunCommand }))
vi.mock('@/shared/i18n', () => ({ t: (key: string) => key }))
vi.mock('@/shared/ui', async () => {
  const { defineComponent, h } = await import('vue')
  const field = (tag: string) => defineComponent({ props: ['modelValue'], setup: (props) => () => h(tag, { value: props.modelValue }) })
  return {
    BaseInput: field('input'), BaseTextarea: field('textarea'), BaseSelect: field('select'), SegmentedControl: field('div'),
    BaseButton: defineComponent({ setup: (_props, { attrs, slots }) => () => h('button', { ...attrs, type: 'button' }, slots.default?.()) }),
    InlineCode: defineComponent({ props: ['code', 'language'], setup: (props) => () => h('code', { 'data-language': props.language }, props.code) }),
    CodeBlock: defineComponent({ props: ['code', 'language', 'lineNumbers'], setup: (props) => () => h('section', { 'data-codeblock': props.language, 'data-line-numbers': String(props.lineNumbers) }, [h('pre', props.code), h('button', { type: 'button', 'data-copy': '' }, 'Copy')]) }),
  }
})

interface Bindings {
  editing: Ref<boolean>
  args: Ref<string>
  target: Ref<string | null>
  workingDirectory: Ref<string>
  patch: (changes: Partial<RunSpec>) => void
  setKind: (kind: RunKind) => void
  toggleEditing: () => void
  useAutomatic: () => void
  selectCandidate: (id: string) => void
}

const script = (): RunSpec => ({ kind: 'script', target: 'experiment.py', args: ['--input', 'my data.csv'], workingDirectory: '.' })
const candidate = (): LaunchCandidate => ({ id: 'candidate', runSpec: script(), reason: 'Detected main guard', confidence: 'high' })

async function render(runSpec: RunSpec | null, options: { editing?: boolean; disabled?: boolean; override?: boolean; candidates?: LaunchCandidate[] } = {}) {
  const update = vi.fn()
  let bindings!: Bindings
  const setup = RunSettings.setup!
  const component = {
    ...RunSettings,
    setup(props: Parameters<typeof setup>[0], context: Parameters<typeof setup>[1]) {
      const result = setup(props, context)
      bindings = result as unknown as Bindings
      if (options.editing) bindings.editing.value = true
      return result
    },
  } as typeof RunSettings
  const app = createSSRApp({ render: () => h(component, {
    runSpec, entrypoints: ['experiment.py'], scriptFiles: ['experiment.py'],
    disabled: options.disabled, hasRunOverride: options.override, candidates: options.candidates ?? [candidate()],
    'onUpdate:runSpec': update,
  }) })
  app.config.globalProperties.$t = (key: string) => key
  return { html: await renderToString(app), bindings, update }
}

describe('command-first launch settings', () => {
  it('shows the detected command and Edit while hiding all optional launch controls', async () => {
    const { html, bindings, update } = await render(script())
    expect(html).toContain('data-codeblock="bash" data-line-numbers="false"')
    expect(html).toContain('python experiment.py --input')
    expect(html).toContain('analysis.run.edit')
    for (const control of ['<select', '<input', '<textarea', 'run__candidate', 'analysis.run.kindLabel']) expect(html).not.toContain(control)
    expect(html).not.toContain('python-label')
    expect(update).not.toHaveBeenCalled()
    bindings.toggleEditing()
    expect(bindings.editing.value).toBe(true)
  })

  it('expands controls explicitly and keeps candidate commands free of nested copy buttons', async () => {
    const { html } = await render(script(), { editing: true, override: true })
    expect(html).toContain('analysis.run.done')
    expect(html).toContain('analysis.run.useAutomatic')
    expect(html).toContain('analysis.run.kindLabel')
    expect(html).toContain('<select')
    expect(html).toContain('<textarea')
    const candidate = html.match(/<button[^>]*class="run__candidate"[^>]*>([\s\S]*?)<\/button>/)?.[1]
    expect(candidate).toContain('data-language="bash"')
    expect(candidate).toContain('python experiment.py --input')
    expect(candidate).not.toContain('<button')
    expect(candidate).not.toContain('data-copy')
    expect(html.match(/data-copy/g)).toHaveLength(1)
    expect(html).not.toContain('analysis.run.python')
  })

  it('opens an editor for a missing command without inventing or emitting a launch', async () => {
    const { html, update } = await render(null)
    expect(html).toContain('analysis.run.unknown')
    expect(html).toContain('<select')
    expect(html).toContain('run__candidate')
    expect(html).not.toContain('data-codeblock')
    expect(html).not.toContain('python …')
    expect(update).not.toHaveBeenCalled()
  })

  it.each([null, script()])('keeps read-only settings free of editing controls or model mutations', async (runSpec) => {
    const { html, bindings, update } = await render(runSpec, { disabled: true, editing: true, override: true })
    for (const control of ['<select', '<input', '<textarea', 'run__candidate', 'analysis.run.edit', 'analysis.run.done', 'analysis.run.useAutomatic']) expect(html).not.toContain(control)
    bindings.patch({ target: 'changed.py' })
    bindings.args.value = '--different'
    bindings.workingDirectory.value = 'other'
    bindings.setKind('module')
    bindings.selectCandidate('candidate')
    bindings.useAutomatic()
    bindings.toggleEditing()
    expect(update).not.toHaveBeenCalled()
    if (!runSpec) expect(html).toContain('analysis.run.unknown')
  })

  it('preserves a manual module command, arguments and working directory during editing', async () => {
    const runSpec: RunSpec = { kind: 'module', target: 'lab.experiment', args: ['--input', 'my data.csv', '--seed=42'], workingDirectory: 'research folder' }
    const { html, bindings, update } = await render(runSpec, { editing: true, override: true })
    expect(html).toContain('python -m lab.experiment --input')
    expect(html).toContain('research folder')
    expect(bindings.args.value).toBe('--input\nmy data.csv\n--seed=42')
    expect(bindings.workingDirectory.value).toBe('research folder')
    bindings.target.value = 'lab.alternate'
    expect(update).toHaveBeenLastCalledWith({ ...runSpec, target: 'lab.alternate' })
    expect(runSpec.target).toBe('lab.experiment')
    bindings.args.value = '--input\n  spaced data.csv  \n--seed=8'
    expect(update).toHaveBeenLastCalledWith({ ...runSpec, args: ['--input', '  spaced data.csv  ', '--seed=8'] })
    bindings.useAutomatic()
    expect(update).toHaveBeenLastCalledWith(null)
  })

  it('uses only an offered candidate and copies its arguments instead of mutating it', async () => {
    const offered = candidate()
    const { bindings, update } = await render(null, { candidates: [offered] })
    bindings.selectCandidate('invented')
    expect(update).not.toHaveBeenCalled()
    bindings.selectCandidate(offered.id)
    const selected = update.mock.lastCall?.[0] as RunSpec
    expect(selected).toEqual(offered.runSpec)
    expect(selected.args).not.toBe(offered.runSpec.args)
  })
})
