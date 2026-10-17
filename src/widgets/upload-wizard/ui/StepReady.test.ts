import { describe, expect, it, vi } from 'vitest'
import { createSSRApp, h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import type { PackagePlan } from '@/entities/project/model/types'
import StepReady from './StepReady.vue'

const { state } = vi.hoisted(() => ({ state: { wizard: {} as Record<string, unknown> } }))

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('../model/uploadWizardStore', () => ({ useUploadWizardStore: () => state.wizard }))
vi.mock('@/shared/i18n', () => ({ t: (key: string) => key, intlLocale: { value: 'ru' } }))
vi.mock('@/features/archive-actions', () => ({
  useArchiveActions: () => ({ error: { value: null }, isPending: () => false }),
}))
vi.mock('@/entities/project', async () => {
  const { h } = await import('vue')
  return { ArchiveTree: () => h('div'), FindingList: () => h('div'), getArchiveFileName: () => 'experiment.zip' }
})

async function render(plan: PackagePlan, lockedPackages?: string[]) {
  state.wizard = {
    project: { id: 'project', name: 'experiment', archive: { status: 'ready', files: [], sizeBytes: 1000 } },
    plan,
    decisions: { excludedFiles: [] },
    lock: lockedPackages ? { packages: lockedPackages, target: { python_version: '3.11' } } : null,
  }
  const app = createSSRApp({ render: () => h(StepReady) })
  app.config.globalProperties.$t = (key: string) => key
  const html = await renderToString(app)
  return html.match(/<dt[^>]*>ready.summary.packages<\/dt>\s*<dd[^>]*>(\d+)<\/dd>/)?.[1]
}

describe('ready archive requirement count', () => {
  it('counts an unpinned draft dependency even when the resolver returned no packages', async () => {
    const count = await render({
      state: 'draft', requirements: ['numpy'], findings: [],
      automaticResolution: { status: 'unavailable', packages: [] },
    })
    expect(count).toBe('1')
  })

  it('counts requirements.txt entries rather than transitive packages in the lock', async () => {
    const count = await render({
      state: 'ready_to_verify', requirements: ['numpy', 'pandas'], findings: [],
      automaticResolution: { status: 'ready', packages: ['numpy==2.4.6', 'pandas==3.0.6', 'python-dateutil==2.9.0.post0', 'six==1.17.0'] },
    }, ['numpy==2.4.6', 'pandas==3.0.6', 'python-dateutil==2.9.0.post0', 'six==1.17.0'])
    expect(count).toBe('2')
  })

  it('keeps zero for a project without external requirements', async () => {
    expect(await render({ state: 'draft', requirements: [], findings: [] })).toBe('0')
  })
})
