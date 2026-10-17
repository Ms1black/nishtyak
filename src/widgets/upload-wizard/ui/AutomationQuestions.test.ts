import { describe, expect, it, vi } from 'vitest'
import { createSSRApp, h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import type { AutomationQuestion, LaunchCandidate } from '@/entities/project/model/types'
import Questions from './AutomationQuestions.vue'

vi.mock('@/entities/project', async () => ({
  formatRunCommand: (await import('@/entities/project/lib/decisions')).formatRunCommand,
}))

async function render(questions: AutomationQuestion[], candidates: LaunchCandidate[] = [], disabled = false) {
  const app = createSSRApp({ render: () => h(Questions, { questions, candidates, questionnaireId: 'batch', disabled }) })
  app.config.globalProperties.$t = (key: string) => key
  return renderToString(app)
}

describe('one project questionnaire', () => {
  it('shows every unresolved question in one form with one submit and a shared optional comment', async () => {
    const html = await render([
      { id: 'mapping-a', aspect: 'dependencies', kind: 'package_mapping', module: 'first' },
      { id: 'mapping-b', aspect: 'dependencies', kind: 'package_mapping', module: 'second' },
      { id: 'data', aspect: 'description', kind: 'text', message: 'Where are the input data?' },
    ])
    expect(html.match(/<form /g)).toHaveLength(1)
    expect(html.match(/type="submit"/g)).toHaveLength(1)
    expect(html.match(/<input /g)).toHaveLength(2)
    expect(html.match(/<textarea /g)).toHaveLength(1)
    expect(html).toContain('Where are the input data?')
    expect(html).toContain('analysis.automation.finalPassHint')
    expect(html).not.toContain(' required')
    expect(html).not.toContain('type="checkbox"')
  })

  it('offers only known candidates allowed by the question and an explicit unknown choice', async () => {
    const candidates: LaunchCandidate[] = ['experiment', 'preview'].map((name) => ({
      id: name,
      runSpec: { kind: 'script', target: `${name}.py`, args: [], workingDirectory: '.' },
      reason: name,
      confidence: 'high',
    }))
    const html = await render([
      { id: 'launch', aspect: 'entrypoint', kind: 'entrypoint', candidateIds: ['experiment', 'invented'] },
    ], candidates)
    expect(html).toContain('python experiment.py')
    expect(html).not.toContain('preview.py')
    expect(html).not.toContain('value="invented"')
    expect(html).toContain('analysis.automation.unknown')
    expect(html.match(/type="radio"/g)).toHaveLength(2)
  })

  it('shows text and unbound constraints without inventing answer fields', async () => {
    const html = await render([
      { id: 'text', aspect: 'description', kind: 'text' },
      { id: 'unbound', aspect: 'environment', kind: 'version_constraint' },
    ])
    expect(html).not.toContain('<input ')
    expect(html.match(/analysis.automation.openSettings/g)).toHaveLength(1)
    expect(html).toContain('analysis.automation.continue')
    expect(html).toContain('clarification-notes')
  })

  it('distinguishes full requirements from module version constraints and escapes source messages', async () => {
    const html = await render([
      { id: 'declaration', aspect: 'environment', kind: 'version_constraint', declarationId: 'numpy', message: '<script>unexpected()</script>' },
      { id: 'version', aspect: 'environment', kind: 'version_constraint', module: 'pandas' },
    ])
    expect(html).not.toContain('<script>')
    expect(html).toContain('&lt;script&gt;unexpected()&lt;/script&gt;')
    expect(html).toContain('analysis.automation.requirementHint')
    expect(html).toContain('analysis.automation.versionHint')
  })

  it('disables fields and the sole submit for readers or an active request', async () => {
    const html = await render([{ id: 'mapping', aspect: 'dependencies', kind: 'package_mapping', module: 'custom' }], [], true)
    expect(html).toContain('<fieldset class="question__item" disabled')
    expect(html).toMatch(/type="submit"[^>]*disabled/)
  })
})
