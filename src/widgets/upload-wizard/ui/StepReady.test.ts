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
vi.mock('./ArchiveReview.vue', () => ({ default: () => null }))
vi.mock('@/entities/project', async () => {
  const { h } = await import('vue')
  return {
    ArchiveTree: () => h('div'), FindingList: () => h('div'), getArchiveFileName: () => 'experiment.zip',
    isPackageBlocked: (await import('@/entities/project/lib/packageState')).isPackageBlocked,
  }
})

async function renderHtml(plan: PackagePlan | null, lockedPackages?: string[], extra: Record<string, unknown> = {}) {
  state.wizard = {
    project: { id: 'project', name: 'experiment', archive: { status: 'ready', files: [], sizeBytes: 1000 } },
    plan,
    decisions: { excludedFiles: [] },
    lock: lockedPackages ? { packages: lockedPackages, target: { python_version: '3.11' } } : null,
    ...extra,
  }
  const app = createSSRApp({ render: () => h(StepReady) })
  app.config.globalProperties.$t = (key: string) => key
  return renderToString(app)
}

async function render(plan: PackagePlan, lockedPackages?: string[]) {
  const html = await renderHtml(plan, lockedPackages)
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

  it('does not advertise a ready archive or zero requirements before the plan has loaded', async () => {
    const html = await renderHtml(null, undefined, { isLoading: true })
    expect(html).toContain('ready.loadingDetails')
    expect(html).not.toContain('ready.summary.packages')
    expect(html).not.toContain('ready.pythonUnknown')
    expect(html).not.toMatch(/>ready.title<\/h2>/)
  })

  it('shows a retriable plan error without invented ready metadata', async () => {
    const html = await renderHtml(null, undefined, { isLoading: false, error: 'Plan temporarily unavailable' })
    expect(html).toContain('ready.detailsUnavailable')
    expect(html).toContain('Plan temporarily unavailable')
    expect(html).toContain('common.retry')
    expect(html).not.toContain('ready.summary.packages')
    expect(html).not.toMatch(/>ready.title<\/h2>/)
  })

  it('preserves the server draft status even when dependency versions are resolved', async () => {
    const html = await renderHtml({
      state: 'ready_to_verify', requirements: ['numpy'], findings: [],
      automation: { phase: 'draft', stage: 'complete', humanRound: 1 },
      automaticResolution: { status: 'ready', packages: ['numpy==2.4.6'] },
    })
    expect(html).toContain('ready.titleDraft')
    expect(html).not.toMatch(/>ready.title<\/h2>/)
  })

  it('keeps a resolved environment ready when the server reports ready despite unknown historical versions', async () => {
    const html = await renderHtml({
      state: 'draft', requirements: ['numpy'], findings: [],
      automation: { phase: 'ready', stage: 'complete', humanRound: 1 },
      automaticResolution: { status: 'ready', packages: ['numpy==2.4.6'] },
    })
    expect(html).toMatch(/>ready.title<\/h2>/)
    expect(html).not.toContain('ready.titleDraft')
  })

  it('does not call a blocked project a ready draft or offer its stale archive', async () => {
    const html = await renderHtml({ state: 'blocked', requirements: [], findings: [], automation: { phase: 'draft' } })
    expect(html).toContain('analysis.plan.blocked.title')
    expect(html).not.toContain('ready.titleDraft')
    expect(html).not.toContain('ready.download')
  })

  it('shows the actual target OS alongside Python', async () => {
    const html = await renderHtml({
      state: 'draft', requirements: [], findings: [],
      automaticResolution: { status: 'ready', target: { pythonVersion: '3.12', platform: 'macos-arm64' } },
    })
    expect(html).toContain('ready.summary.platform')
    expect(html).toContain('macos-arm64')
    expect(html).toContain('3.12')
  })
})
