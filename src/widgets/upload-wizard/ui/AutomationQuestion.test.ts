import { describe, expect, it, vi } from 'vitest'
import { createSSRApp, h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import type { AutomationQuestion, LaunchCandidate } from '@/entities/project/model/types'
import Question from './AutomationQuestion.vue'

vi.mock('@/entities/project', async () => ({
  formatRunCommand: (await import('@/entities/project/lib/decisions')).formatRunCommand,
}))

async function render(question: AutomationQuestion, candidates: LaunchCandidate[] = []) {
  const app = createSSRApp({ render: () => h(Question, { question, candidates }) })
  app.config.globalProperties.$t = (key: string) => key
  return renderToString(app)
}

describe('focused automation questions', () => {
  it('shows one package field without asking the author to assemble an environment', async () => {
    const html = await render({ id: 'mapping', aspect: 'dependencies', kind: 'package_mapping', module: 'sklearn' })
    expect(html.match(/<input /g)).toHaveLength(1)
    expect(html).not.toContain('type="checkbox"')
    expect(html).not.toContain('python-version')
    expect(html).toContain('analysis.automation.continue')
  })

  it('offers only launch candidates explicitly supplied for the server question', async () => {
    const candidates: LaunchCandidate[] = ['experiment', 'preview'].map((name) => ({
      id: name,
      runSpec: { kind: 'script', target: `${name}.py`, args: [], workingDirectory: '.' },
      reason: name,
      confidence: 'high',
    }))
    const html = await render({
      id: 'launch', aspect: 'entrypoint', kind: 'entrypoint', candidateIds: ['experiment'],
    }, candidates)
    expect(html).toContain('python experiment.py')
    expect(html).not.toContain('preview.py')
    expect(html).not.toContain('<input ')
  })

  it.each(['text', 'version_constraint'] as const)('does not collect an unbound %s answer', async (kind) => {
    const html = await render({ id: 'unbound', aspect: 'environment', kind })
    expect(html).not.toContain('<input ')
    expect(html).toContain('analysis.automation.openSettings')
    expect(html).not.toContain('analysis.automation.continue')
  })

  it('renders explanatory messages as escaped text', async () => {
    const html = await render({
      id: 'constraint', aspect: 'environment', kind: 'version_constraint',
      declarationId: 'numpy', message: '<script>unexpected()</script>',
    })
    expect(html).not.toContain('<script>')
    expect(html).toContain('&lt;script&gt;unexpected()&lt;/script&gt;')
    expect(html).toContain('<input ')
  })
})
