import { describe, expect, it, vi } from 'vitest'
import { createSSRApp, h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import ProjectFileExplorer from './ProjectFileExplorer.vue'

vi.mock('@/entities/project', async () => ({
  buildFileTree: (await import('@/entities/project/lib/fileTree')).buildFileTree,
  getSecretLabel: () => 'API token',
}))
vi.mock('@/shared/i18n', () => ({ t: (key: string) => key }))
vi.mock('@/shared/ui', async () => ({ BaseIcon: (await import('vue')).defineComponent({ render: () => null }) }))
vi.mock('./FileViewer.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return { default: defineComponent({ props: ['projectId', 'analysisId', 'file', 'disabled', 'excluded'], setup: (props) => () => h('section', {
    'data-project': props.projectId, 'data-analysis': props.analysisId, 'data-path': props.file?.path,
    'data-read-only': String(props.disabled), 'data-excluded': String(props.excluded),
  }) }) }
})

describe('project file explorer preview context', () => {
  it('forwards project, analysis, secret exclusions and editing permissions to the source viewer', async () => {
    const app = createSSRApp({ render: () => h(ProjectFileExplorer, {
      projectId: 'current-project', analysisId: 'new-analysis', projectName: 'Experiment',
      files: [{ path: 'experiment.py', size: 100 }, { path: 'config.py', size: 40, secret: 'api_token' }],
      excludedFiles: ['config.py'], disabled: true,
    }) })
    app.config.globalProperties.$t = (key: string) => key
    const html = await renderToString(app)
    expect(html).toContain('data-project="current-project" data-analysis="new-analysis" data-path="config.py" data-read-only="true" data-excluded="true"')
  })
})
