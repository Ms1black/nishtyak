import { describe, expect, it, vi } from 'vitest'
import { createSSRApp, h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import ProjectDropzone from './ProjectDropzone.vue'

vi.mock('@/shared/i18n', () => ({ t: (key: string) => key, intlLocale: { value: 'en-US' } }))

async function render(disabled = false) {
  const select = vi.fn()
  let handlers!: { onInputChange: (event: unknown) => void; onDrop: (event: unknown) => void }
  const setup = ProjectDropzone.setup!
  const component = {
    ...ProjectDropzone,
    setup(props: Parameters<typeof setup>[0], context: Parameters<typeof setup>[1]) {
      const result = setup(props, context)
      handlers = result as unknown as typeof handlers
      return result
    },
  } as typeof ProjectDropzone
  const app = createSSRApp({ render: () => h(component, { file: null, disabled, onSelect: select }) })
  app.config.globalProperties.$t = (key: string) => key
  const html = await renderToString(app)
  return { html, handlers, select }
}

describe('project file picker and drop events', () => {
  it('offers ZIP only and passes the entire selection for explicit rejection', async () => {
    const { html, handlers, select } = await render()
    expect(html).toContain('accept=".zip"')
    expect(html).not.toContain(' multiple')
    const files = [new File(['one'], 'one.zip'), new File(['two'], 'two.zip')]
    const target = { files, value: 'selected-files' }
    handlers.onInputChange({ target })
    expect(select).toHaveBeenCalledExactlyOnceWith(files)
    expect(target.value).toBe('')
  })

  it('passes all dropped files and ignores events when disabled', async () => {
    const files = [new File(['one'], 'one.zip'), new File(['two'], 'two.zip')]
    const active = await render()
    active.handlers.onDrop({ dataTransfer: { files } })
    expect(active.select).toHaveBeenCalledExactlyOnceWith(files)
    const disabled = await render(true)
    disabled.handlers.onDrop({ dataTransfer: { files } })
    disabled.handlers.onInputChange({ target: { files, value: 'selected' } })
    expect(disabled.select).not.toHaveBeenCalled()
    expect(disabled.html).toMatch(/accept=".zip" disabled/)
  })
})
