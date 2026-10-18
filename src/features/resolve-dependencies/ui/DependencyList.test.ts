// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { createSSRApp, h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import type { Dependency } from '@/entities/project'
import DependencyList from './DependencyList.vue'

const dependencies: Dependency[] = [
  { id: 'numpy', module: 'numpy', package: 'numpy', requirement: 'numpy>=1.26', category: 'external', source: 'declared', locations: [] },
  { id: 'mystery', module: 'lab_unknown', package: null, requirement: null, category: 'unresolved', source: 'unresolved', locations: [] },
  { id: 'local', module: 'helpers', package: null, requirement: null, category: 'local', source: 'inferred', locations: [] },
]

async function render(editor = false) {
  const app = createSSRApp({ render: () => h(DependencyList, {
    dependencies, packages: {}, versions: {}, excludedModules: [],
    requirements: ['numpy==2.2.6'], unresolvedModules: ['lab_unknown'], editor,
  }) })
  app.config.globalProperties.$t = (key: string) => key
  const root = document.createElement('div')
  root.innerHTML = await renderToString(app)
  return root
}

describe('dependency result and explicit expert editor', () => {
  it('shows prepared requirements and unknown imports readonly without a second question form', async () => {
    const root = await render()
    expect(root.textContent).toContain('numpy')
    expect(root.textContent).toContain('==2.2.6')
    expect(root.textContent).toContain('lab_unknown')
    expect(root.textContent).toContain('analysis.deps.unknownPackage')
    expect(root.querySelector('input, select, form, button')).toBeNull()
    expect(root.textContent).not.toContain('analysis.deps.unresolvedHint')
  })

  it('opens package and constraint controls only when the parent explicitly requests expert editing', async () => {
    const root = await render(true)
    expect(root.querySelectorAll('input')).toHaveLength(4)
    expect(root.querySelector('.mapping, form')).toBeNull()
    expect(root.querySelectorAll('.dep__toggle')).toHaveLength(2)
  })
})
