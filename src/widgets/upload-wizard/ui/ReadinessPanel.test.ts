import { describe, expect, it, vi } from 'vitest'
import { createSSRApp, h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import type { PackagePlan, Project } from '@/entities/project/model/types'
import ReadinessPanel from './ReadinessPanel.vue'

vi.mock('@/shared/i18n', () => ({ t: (key: string) => key, intlLocale: { value: 'ru' } }))
vi.mock('@/entities/project', async () => {
  const { h } = await import('vue')
  return {
    AnalysisBadge: (props: { status: string }) => h('span', `static-analysis-${props.status}`),
    ProjectSourceIcon: () => h('span'),
    getProjectMeta: (project: Project) => [`${project.filesCount} source files`],
  }
})

const project: Project = {
  id: 'project', name: 'experiment', sourceType: 'zip', sizeBytes: 1000, filesCount: 3,
  analysis: 'full', archive: { status: 'not_built' }, updatedAt: '2026-10-17T00:00:00Z',
}
const plan: PackagePlan = { state: 'ready_to_verify', requirements: [], findings: [] }

async function render(currentPlan: PackagePlan, extra: Record<string, unknown> = {}) {
  const app = createSSRApp({ render: () => h(ReadinessPanel, {
    project, plan: currentPlan, saveState: 'idle', savedAt: null, isBuilding: false,
    isRefreshing: false, canBuild: false, error: null, ...extra,
  }) })
  app.config.globalProperties.$t = (key: string) => key
  return renderToString(app)
}

describe('package readiness status', () => {
  it('waits for the answer instead of advertising a ready package when static analysis is complete', async () => {
    const html = await render({ ...plan, automation: { phase: 'needs_input' } })
    expect(html).toContain('analysis.automation.awaitingAnswer')
    expect(html).toContain('analysis.automation.needsAnswer')
    expect(html).not.toContain('analysis.plan.ready_to_verify')
    expect(html).not.toContain('static-analysis-full')
    expect(html).toContain('panel__state--needs_input')
  })

  it('shows preparation progress instead of ready status during a server retry', async () => {
    const html = await render({ ...plan, automation: { phase: 'assisting' } }, {
      isProcessing: true, progressText: 'Retrying dependency analysis',
    })
    expect(html).toContain('Retrying dependency analysis')
    expect(html).toContain('analysis.automation.preparing')
    expect(html).not.toContain('analysis.plan.ready_to_verify')
    expect(html).not.toContain('static-analysis-full')
  })

  it('keeps a draft honest even if static analysis is complete', async () => {
    const html = await render({ ...plan, automation: { phase: 'draft' } })
    expect(html).toContain('analysis.plan.draft.title')
    expect(html).not.toContain('static-analysis-full')
    expect(html).not.toContain('analysis.plan.ready_to_verify')
  })

  it('shows validation while an answer is being saved', async () => {
    const html = await render({ ...plan, automation: { phase: 'needs_input' } }, { isRefreshing: true })
    expect(html).toContain('analysis.automation.validatingAnswer')
    expect(html).not.toContain('analysis.automation.awaitingAnswer')
  })
})
