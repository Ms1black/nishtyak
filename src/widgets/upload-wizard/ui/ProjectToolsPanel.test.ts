// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, reactive } from 'vue'
import type { ProjectAnalysis, TargetEnvironment } from '@/entities/project/model/types'
import ProjectToolsPanel from './ProjectToolsPanel.vue'

const { state } = vi.hoisted(() => ({ state: { wizard: {} as Record<string, unknown> } }))
vi.mock('../model/uploadWizardStore', () => ({ useUploadWizardStore: () => state.wizard }))
vi.mock('@/shared/i18n', () => ({ t: (key: string) => key }))
vi.mock('@/shared/ui', () => ({
  BaseSelect: defineComponent({
    props: ['modelValue', 'options', 'disabled'], emits: ['update:modelValue'],
    setup: (props, { emit, attrs }) => () => h('select', {
      ...attrs, disabled: props.disabled, value: props.modelValue,
      onChange: (event: Event) => emit('update:modelValue', (event.target as HTMLSelectElement).value),
    }, props.options.map((option: { value: string; label: string }) => h('option', { value: option.value }, option.label))),
  }),
  BaseButton: defineComponent({
    props: ['disabled', 'loading'],
    setup: (props, { slots, attrs }) => () => h('button', { ...attrs, disabled: props.disabled || props.loading }, slots.default?.()),
  }),
  BaseInput: defineComponent({
    props: ['modelValue', 'disabled'], emits: ['update:modelValue'],
    setup: (props, { emit }) => () => h('input', {
      value: props.modelValue, disabled: props.disabled,
      onInput: (event: Event) => emit('update:modelValue', (event.target as HTMLInputElement).value),
    }),
  }),
  BaseCheckbox: defineComponent({ props: ['disabled'], setup: (props, { slots }) => () => h('label', [h('input', { type: 'checkbox', disabled: props.disabled }), slots.default?.()]) }),
  CodeBlock: defineComponent({ props: ['code'], setup: (props) => () => h('pre', props.code) }),
}))

let dispose: (() => void) | undefined
let container: HTMLDivElement

function mount() {
  container = document.createElement('div')
  document.body.append(container)
  const app = createApp({ render: () => h(ProjectToolsPanel) })
  app.config.globalProperties.$t = (key: string) => key
  app.mount(container)
  dispose = () => { app.unmount(); container.remove() }
  return container
}

function button(key: string) {
  return [...container.querySelectorAll('button')].find((item) => item.textContent?.trim() === key)!
}

async function choose(label: string, value: string) {
  const input = container.querySelector<HTMLSelectElement>(`select[aria-label="${label}"]`)!
  input.value = value
  input.dispatchEvent(new Event('change'))
  await nextTick()
}

describe('single target environment control', () => {
  beforeEach(() => {
    state.wizard = reactive({
      analysis: {
        analysisId: 'analysis', options: { pythonVersion: '3.11', targetPlatform: 'macos-arm64', projectRoot: null, sourceRoots: ['.'] },
        projectRoots: ['.'], optionalGroups: [], pythonRequires: '>=3.10',
      } as Partial<ProjectAnalysis>,
      targetEnvironment: { pythonVersion: '3.11', platform: 'macos-arm64' } as TargetEnvironment,
      decisions: { selectedExtras: [] }, plan: null, automation: null, lock: null,
      canEdit: true, isBusy: false, isRefreshing: false, canIncludeDockerfile: true,
      applyTarget: vi.fn(), reanalyze: vi.fn(), toggleExtra: vi.fn(), includeDockerfile: false,
    })
  })
  afterEach(() => { dispose?.(); dispose = undefined })

  it('shows only Python and OS as environment selectors, with diagnostics closed and no resolver action', async () => {
    mount()
    expect(container.querySelectorAll('select')).toHaveLength(2)
    expect(container.querySelector('details')?.open).toBe(false)
    expect(container.textContent).toContain('workflow.analysisProfile')
    expect(container.textContent).not.toContain('workflow.resolve')
    expect(button('workflow.applyTarget').disabled).toBe(true)
    await choose('workflow.targetPython', '3.12')
    await choose('workflow.targetPlatform', 'windows-x86_64')
    button('workflow.applyTarget').click()
    expect(state.wizard.applyTarget).toHaveBeenCalledExactlyOnceWith({ pythonVersion: '3.12', platform: 'windows-x86_64' })
    expect(state.wizard.reanalyze).not.toHaveBeenCalled()
  })

  it('hydrates the persisted environment when analysis changes without submitting it again', async () => {
    mount()
    state.wizard.analysis = { ...(state.wizard.analysis as object), analysisId: 'next' }
    state.wizard.targetEnvironment = { pythonVersion: '3.13', platform: 'linux-aarch64' }
    await nextTick()
    const selectors = [...container.querySelectorAll('select')]
    expect(selectors.map((item) => item.value)).toEqual(['3.13', 'linux-aarch64'])
    expect(button('workflow.applyTarget').disabled).toBe(true)
    expect(state.wizard.applyTarget).not.toHaveBeenCalled()
  })

  it('keeps the applied target during a diagnostic rerun, rather than the unsubmitted form draft', async () => {
    mount()
    await choose('workflow.targetPython', '3.12')
    button('workflow.reanalyze').click()
    expect(state.wizard.reanalyze).toHaveBeenCalledExactlyOnceWith({
      pythonVersion: '3.11', targetPlatform: 'macos-arm64', projectRoot: null, sourceRoots: ['.'],
    }, undefined)
    expect(state.wizard.applyTarget).not.toHaveBeenCalled()
  })

  it('keeps an unsubmitted target draft when an unrelated autosave replaces the plan target', async () => {
    mount()
    state.wizard.targetEnvironment = null
    await nextTick()
    await choose('workflow.targetPython', '3.12')
    await choose('workflow.targetPlatform', 'windows-x86_64')
    state.wizard.targetEnvironment = { pythonVersion: '3.11', platform: 'macos-arm64' }
    await nextTick()
    expect([...container.querySelectorAll('select')].map((item) => item.value)).toEqual(['3.12', 'windows-x86_64'])
    button('workflow.applyTarget').click()
    expect(state.wizard.applyTarget).toHaveBeenCalledExactlyOnceWith({ pythonVersion: '3.12', platform: 'windows-x86_64' })
  })

  it('retains an explicit Python patch version in the selector without normalizing it away', () => {
    state.wizard.targetEnvironment = { pythonVersion: '3.11.9', platform: 'macos-arm64' }
    mount()
    expect(container.querySelector<HTMLSelectElement>('select[aria-label="workflow.targetPython"]')?.value).toBe('3.11.9')
    expect(button('workflow.applyTarget').disabled).toBe(true)
  })

  it('cannot bypass the shared clarification form by changing the target', async () => {
    state.wizard.automation = { phase: 'needs_input' }
    mount()
    expect(container.textContent).toContain('workflow.targetAfterQuestions')
    expect([...container.querySelectorAll('select')].every((item) => item.disabled)).toBe(true)
    expect(button('workflow.applyTarget').disabled).toBe(true)
    expect(button('workflow.reanalyze').disabled).toBe(true)
    button('workflow.applyTarget').click()
    expect(state.wizard.applyTarget).not.toHaveBeenCalled()
  })

  it('lets a viewer inspect the environment but offers no apply or rerun action', () => {
    state.wizard.canEdit = false
    mount()
    expect(container.textContent).toContain('workflow.targetTitle')
    expect(container.textContent).not.toContain('workflow.applyTarget')
    expect(container.textContent).not.toContain('workflow.reanalyze')
    expect([...container.querySelectorAll('select,input')].every((item) => (item as HTMLInputElement).disabled)).toBe(true)
  })
})
