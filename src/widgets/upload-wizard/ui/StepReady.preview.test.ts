// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, reactive, type App } from 'vue'
import StepReady from './StepReady.vue'

const { dependencies } = vi.hoisted(() => ({
  dependencies: { wizard: {} as Record<string, unknown>, download: vi.fn() },
}))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('../model/uploadWizardStore', () => ({ useUploadWizardStore: () => dependencies.wizard }))
vi.mock('@/shared/i18n', () => ({ t: (key: string) => key, intlLocale: { value: 'en-US' } }))
vi.mock('@/features/archive-actions', () => ({
  useArchiveActions: () => ({ error: { value: null }, isPending: () => false, download: dependencies.download }),
}))
vi.mock('./ArchiveReview.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return { default: defineComponent({
    emits: ['open-file', 'refresh'],
    setup: (_props, { emit }) => () => h('div', [
      h('button', { 'data-readme': '', onClick: () => emit('open-file', 'README.md') }, 'README'),
      h('button', { 'data-unlisted': '', onClick: () => emit('open-file', 'not-in-archive.py') }, 'Unknown'),
    ]),
  }) }
})
vi.mock('./ArchiveFileViewer.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return { default: defineComponent({
    props: ['open', 'path', 'archiveTag', 'projectId'],
    setup: (props) => () => h('div', {
      'data-archive-viewer': '', 'data-open': String(props.open),
      'data-path': props.path, 'data-sha': props.archiveTag,
    }),
  }) }
})
vi.mock('@/entities/project', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    ArchiveTree: defineComponent({
      props: ['items'], emits: ['select'],
      setup: (_props, { emit }) => () => h('button', {
        'data-tree-file': '', onClick: () => emit('select', 'project/experiment.py'),
      }, 'experiment.py'),
    }),
    FindingList: () => null,
    getArchiveFileName: () => 'experiment.zip',
    isPackageBlocked: (await import('@/entities/project/lib/packageState')).isPackageBlocked,
  }
})

let application: App | undefined
let container: HTMLElement
const TAG = 'a'.repeat(64)
function mount(hash: string | undefined = TAG) {
  const project = reactive({
    id: 'project', name: 'Experiment', archive: {
      status: 'ready', sha256: hash, files: ['README.md', 'project/experiment.py'], sizeBytes: 100,
    },
  })
  dependencies.wizard = {
    project, plan: { state: 'ready_to_verify', requirements: [], findings: [] },
    decisions: { excludedFiles: [] }, canEdit: true,
  }
  container = document.createElement('div')
  document.body.appendChild(container)
  application = createApp({ render: () => h(StepReady) })
  application.config.globalProperties.$t = (key: string) => key
  application.mount(container)
  return project
}
afterEach(() => {
  application?.unmount()
  container?.remove()
  vi.clearAllMocks()
})

describe('one archive viewer for README and final ZIP members', () => {
  it('opens the same hash-bound viewer from the README action and archive tree, retaining ZIP download', async () => {
    const project = mount()
    const viewer = () => container.querySelector<HTMLElement>('[data-archive-viewer]')!
    expect(container.querySelectorAll('[data-archive-viewer]')).toHaveLength(1)
    container.querySelector<HTMLButtonElement>('[data-readme]')!.click()
    await nextTick()
    expect(viewer().dataset.open).toBe('true')
    expect(viewer().dataset.path).toBe('README.md')
    expect(viewer().dataset.sha).toBe(TAG)
    container.querySelector<HTMLButtonElement>('[data-tree-file]')!.click()
    await nextTick()
    expect(viewer().dataset.path).toBe('project/experiment.py')
    expect(container.querySelectorAll('[data-archive-viewer]')).toHaveLength(1)
    const download = [...container.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent === 'ready.download')!
    download.click()
    expect(dependencies.download).toHaveBeenCalledExactlyOnceWith(project)
  })

  it('does not open an unlisted arbitrary path and clears the selected file when ZIP changes', async () => {
    const project = mount()
    const viewer = () => container.querySelector<HTMLElement>('[data-archive-viewer]')!
    container.querySelector<HTMLButtonElement>('[data-unlisted]')!.click()
    await nextTick()
    expect(viewer().dataset.open).toBe('false')
    container.querySelector<HTMLButtonElement>('[data-readme]')!.click()
    await nextTick()
    project.archive.sha256 = 'b'.repeat(64)
    await nextTick()
    expect(viewer().dataset.open).toBe('false')
    expect(viewer().getAttribute('data-path')).toBeNull()
  })

  it('requires an archive hash for both entry points', async () => {
    mount('')
    container.querySelector<HTMLButtonElement>('[data-readme]')!.click()
    container.querySelector<HTMLButtonElement>('[data-tree-file]')!.click()
    await nextTick()
    expect(container.querySelector<HTMLElement>('[data-archive-viewer]')!.dataset.open).toBe('false')
  })

  it('can request README from the actual ZIP even when older metadata has no file list', async () => {
    const project = mount()
    project.archive.files = []
    await nextTick()
    container.querySelector<HTMLButtonElement>('[data-readme]')!.click()
    await nextTick()
    const viewer = container.querySelector<HTMLElement>('[data-archive-viewer]')!
    expect(viewer.dataset.open).toBe('true')
    expect(viewer.dataset.path).toBe('README.md')
    expect(viewer.dataset.sha).toBe(TAG)
  })
})
