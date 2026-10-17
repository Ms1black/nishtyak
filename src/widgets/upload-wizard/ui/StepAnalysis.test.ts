import { describe, expect, it, vi } from 'vitest'
import { createSSRApp, h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import StepAnalysis from './StepAnalysis.vue'

const { state } = vi.hoisted(() => ({ state: { wizard: {} as Record<string, unknown> } }))
vi.mock('../model/uploadWizardStore', () => ({ useUploadWizardStore: () => state.wizard }))
vi.mock('@/shared/i18n', () => ({ t: (key: string) => key, intlLocale: { value: 'ru' } }))

describe('analysis recovery', () => {
  it('shows a retriable plan failure instead of an empty successful analysis summary', async () => {
    state.wizard = {
      project: { id: 'project', name: 'Experiment' },
      analysis: { analysisId: 'analysis', status: 'full', files: [], findings: [] },
      decisions: { excludedFiles: [] },
      plan: null, error: 'Plan temporarily unavailable', saveState: 'idle',
      isLoading: false, isAnalyzing: false,
    }
    const app = createSSRApp({ render: () => h(StepAnalysis) })
    app.config.globalProperties.$t = (key: string) => key
    const html = await renderToString(app)
    expect(html).toContain('Plan temporarily unavailable')
    expect(html).toContain('common.retry')
    expect(html).not.toContain('analysis.review.title')
    expect(html).not.toContain('analysis.deps.empty')
  })
})
