import { describe, expect, it, vi } from 'vitest'
import { createSSRApp, h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import FindingList from './FindingList.vue'
import type { Finding } from '../model/types'

vi.mock('@/shared/i18n', () => ({
  t: (key: string, params: Record<string, string> = {}) => key === 'findings.DYNAMIC_IMPORT' ? `Dynamic import in ${params.at}` : key,
  intlLocale: { value: 'en-US' },
}))

async function render(findings: Finding[], residualOnly = false, actionable = false) {
  return renderToString(createSSRApp({ render: () => h(FindingList, { findings, residualOnly, actionable }) }))
}

describe('finding evidence presentation', () => {
  it('shows a shared limitation once with all source locations and imports', async () => {
    const html = await render([
      { id: 'numpy', code: 'VERSION_UNKNOWN', severity: 'warning', message: 'Version unknown', module: 'numpy', path: 'experiment.py', line: 2 },
      { id: 'pandas', code: 'VERSION_NOT_PINNED', severity: 'warning', message: 'Version unknown', module: 'pandas', path: 'helpers.py', line: 7 },
    ])
    expect(html.match(/class="finding__text"/g)).toHaveLength(1)
    for (const detail of ['experiment.py:2', 'helpers.py:7', 'import numpy', 'import pandas']) expect(html).toContain(detail)
  })

  it('keeps a location in the explanation without repeating it and escapes filenames', async () => {
    const html = await render([
      { id: 'source', code: 'DYNAMIC_IMPORT', severity: 'warning', message: 'Dynamic import', path: '<script>.py', line: 3 },
    ])
    expect(html).not.toContain('<script>')
    expect(html.match(/&lt;script&gt;\.py:3/g)).toHaveLength(1)
  })

  it('provides keyboard-operable actions for each source only when requested', async () => {
    const findings: Finding[] = [
      { id: 'one', code: 'NEW_DIAGNOSTIC', severity: 'error', message: 'Missing local module', path: 'one.py', line: 4, module: 'lab' },
      { id: 'two', code: 'NEW_DIAGNOSTIC', severity: 'warning', message: 'Missing local module', path: 'two.py', line: 8, module: 'lab' },
    ]
    const actionable = await render(findings, false, true)
    expect(actionable.match(/<button type="button"/g)).toHaveLength(4)
    expect(actionable).toContain('one.py:4')
    expect(actionable).toContain('two.py:8')
    expect(await render(findings, true)).not.toContain('<button')
  })
})
