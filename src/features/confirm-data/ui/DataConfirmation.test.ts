// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, reactive, ref } from 'vue'
import type { Finding } from '@/entities/project'
import DataConfirmation from './DataConfirmation.vue'

const cleanups: Array<() => void> = []
afterEach(() => { cleanups.splice(0).forEach((cleanup) => cleanup()) })

function mount(options: { files?: string[]; findings?: Finding[]; canReplace?: boolean; disabled?: boolean; saved?: boolean } = {}) {
  const values = reactive({
    inputDescription: options.saved ? 'Shared source: data/measurements.csv' : '',
    expectedOutput: options.saved ? 'Author expects a CSV table' : '',
    description: options.saved ? 'Existing author description' : '',
  })
  const replacement = vi.fn()
  const changed = vi.fn()
  const component = ref<InstanceType<typeof DataConfirmation> | null>(null)
  const root = document.createElement('div')
  document.body.append(root)
  const app = createApp({ render: () => h(DataConfirmation, {
    ref: component, dataFiles: options.files ?? [], inputFindings: options.findings ?? [],
    canReplace: options.canReplace, disabled: options.disabled,
    ...values,
    'onUpdate:inputDescription': (value: string) => { changed('inputDescription'); values.inputDescription = value },
    'onUpdate:expectedOutput': (value: string) => { changed('expectedOutput'); values.expectedOutput = value },
    'onUpdate:description': (value: string) => { changed('description'); values.description = value },
    onReplaceSource: replacement,
  }) })
  app.config.globalProperties.$t = (key: string) => key
  app.mount(root)
  cleanups.push(() => { app.unmount(); root.remove() })
  return { root, values, component, changed, replacement }
}

describe('automatic project data facts', () => {
  it('shows archive file facts without a completeness checkbox or default author form', () => {
    const { root, changed } = mount({ files: ['measurements.csv', 'config.yml'] })
    expect(root.textContent).toContain('measurements.csv')
    expect(root.textContent).toContain('config.yml')
    expect(root.querySelector('input[type="checkbox"], textarea')).toBeNull()
    expect(root.querySelector('details')?.open).toBe(false)
    expect(changed).not.toHaveBeenCalled()
  })

  it('does not invent a missing-data diagnosis or an output when no files were found', () => {
    const { root, values } = mount()
    expect(root.textContent).toContain('analysis.data.notFound')
    expect(root.textContent).not.toContain('analysis.data.missingTitle')
    expect(root.textContent).not.toContain('analysis.data.replaceZip')
    expect(values.expectedOutput).toBe('')
  })

  it('shows the concrete unresolved input path once and offers replacement only to editors', () => {
    const message = 'В коде указан входной путь "measurements.csv"; его доступность не подтверждена.'
    const findings: Finding[] = [
      { id: 'first', code: 'INPUT_PATH_UNVERIFIED', severity: 'warning', path: 'experiment.py', message },
      { id: 'second', code: 'INPUT_PATH_UNVERIFIED', severity: 'warning', path: 'helpers.py', message },
    ]
    const editor = mount({ findings, canReplace: true })
    expect(editor.root.querySelectorAll('.data__missing li')).toHaveLength(1)
    expect(editor.root.textContent).toContain('measurements.csv')
    editor.root.querySelector<HTMLButtonElement>('.data__missing button')!.click()
    expect(editor.replacement).toHaveBeenCalledOnce()
    const viewer = mount({ findings })
    expect(viewer.root.textContent).toContain('measurements.csv')
    expect(viewer.root.querySelector('.data__missing button')).toBeNull()
  })

  it('preserves saved author fields while opening, closing and editing optional additions', async () => {
    const { root, values, component, changed } = mount({ saved: true })
    const original = { ...values }
    expect(root.textContent).toContain('analysis.data.savedAdditions')
    expect(root.querySelector('textarea')).toBeNull()
    expect(changed).not.toHaveBeenCalled()
    component.value!.openAdditions()
    await nextTick()
    expect(root.querySelectorAll('textarea')).toHaveLength(3)
    const input = root.querySelector<HTMLTextAreaElement>('#data-input')!
    expect(input.value).toBe(original.inputDescription)
    input.value = 'Author adds the location of the sample dataset'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    expect(values.inputDescription).toBe(input.value)
    expect(values.expectedOutput).toBe(original.expectedOutput)
    expect(values.description).toBe(original.description)
    const details = root.querySelector('details')!
    details.open = false
    details.dispatchEvent(new Event('toggle'))
    await nextTick()
    expect(root.querySelector('textarea')).toBeNull()
    component.value!.openAdditions()
    await nextTick()
    expect(root.querySelector<HTMLTextAreaElement>('#data-input')!.value).toBe(values.inputDescription)
    expect(changed.mock.calls).toEqual([['inputDescription']])
  })

  it('keeps optional author additions readonly while busy or viewed by a reader', async () => {
    const { root, component, changed } = mount({ disabled: true, saved: true })
    component.value!.openAdditions()
    await nextTick()
    expect([...root.querySelectorAll('textarea')].every((field) => field.disabled)).toBe(true)
    expect(changed).not.toHaveBeenCalled()
  })
})
