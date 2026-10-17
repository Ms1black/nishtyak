import { describe, expect, it, vi } from 'vitest'
import { createSSRApp, h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import ArchiveTree from './ArchiveTree.vue'

vi.mock('@/shared/ui', async () => ({ BaseIcon: (await import('vue')).defineComponent({ render: () => null }) }))

async function render(selectable = true, disabled = false) {
  const selected = vi.fn()
  let bindings!: { select: (path: string) => void }
  const setup = ArchiveTree.setup!
  const wrapped = {
    ...ArchiveTree,
    setup(props: Parameters<typeof setup>[0], context: Parameters<typeof setup>[1]) {
      const result = setup(props, context)
      bindings = result as unknown as typeof bindings
      return result
    },
  } as typeof ArchiveTree
  const app = createSSRApp({ render: () => h(wrapped, {
    rootName: 'experiment.zip', items: ['README.md', 'project/lab experiment.py'],
    selectable, disabled, selected: 'project/lab experiment.py', onSelect: selected,
  }) })
  return { html: await renderToString(app), bindings, selected }
}

describe('archive file selection', () => {
  it('offers native keyboard-accessible buttons and emits the exact generated or source file path', async () => {
    const { html, bindings, selected } = await render()
    expect(html.match(/<button/g)).toHaveLength(2)
    expect(html).toContain('type="button"')
    expect(html).toContain('aria-pressed="true"')
    expect(html).toContain('project/lab experiment.py')
    bindings.select('README.md')
    bindings.select('project/lab experiment.py')
    expect(selected.mock.calls).toEqual([['README.md'], ['project/lab experiment.py']])
  })

  it('does not pretend the static example tree can open files', async () => {
    const { html, bindings, selected } = await render(false)
    expect(html).not.toContain('<button')
    bindings.select('README.md')
    expect(selected).not.toHaveBeenCalled()
  })

  it('does not select a file until the archive metadata is usable', async () => {
    const { html, bindings, selected } = await render(true, true)
    expect(html).toContain('disabled')
    bindings.select('README.md')
    expect(selected).not.toHaveBeenCalled()
  })
})
