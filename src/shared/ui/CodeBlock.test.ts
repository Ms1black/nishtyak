// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import { MAX_PREVIEW_CHARACTERS } from '@/shared/lib/rendering/highlight'
import CodeBlock from './CodeBlock.vue'
import InlineCode from './InlineCode.vue'
import MarkdownContent from './MarkdownContent.vue'
import BaseDialog from './BaseDialog.vue'

vi.mock('@/shared/i18n', () => ({ t: (key: string) => key }))
let application: App | undefined
let container: HTMLElement | undefined
function mount(component: Parameters<typeof h>[0], props: Record<string, unknown>) {
  container = document.createElement('div')
  document.body.appendChild(container)
  application = createApp({ render: () => h(component, props) })
  application.mount(container)
  return container
}
afterEach(() => {
  application?.unmount()
  container?.remove()
  vi.unstubAllGlobals()
})

describe('shared source and document presentation', () => {
  it('highlights file code with escaped filename and default line numbers', () => {
    const element = mount(CodeBlock, { code: 'import numpy\nprint(1)', filename: '<img src=x>.py' })
    expect(element.querySelector('.hljs-keyword')?.textContent).toBe('import')
    expect(element.querySelectorAll('.code-block__numbers span')).toHaveLength(2)
    expect(element.querySelector('.code-block__filename')?.textContent).toBe('<img src=x>.py')
    expect(element.querySelector('img')).toBeNull()
  })

  it('copies the complete original text when its preview is truncated', async () => {
    const code = 'x'.repeat(MAX_PREVIEW_CHARACTERS + 10)
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    const element = mount(CodeBlock, { code, language: 'text', lineNumbers: false })
    expect(element.querySelector('.code-block__numbers')).toBeNull()
    expect(element.querySelector('code')?.textContent).toHaveLength(MAX_PREVIEW_CHARACTERS)
    const button = element.querySelector<HTMLButtonElement>(
      'button[aria-label="rendering.copyCode"]',
    )!
    button.click()
    await nextTick()
    await Promise.resolve()
    expect(writeText).toHaveBeenCalledExactlyOnceWith(code)
    expect(element.querySelector('[role="status"]')?.textContent).toBe('rendering.copied')
  })

  it('exposes keyboard-accessible wrap control and reports clipboard rejection honestly', async () => {
    vi.stubGlobal('navigator', {
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error('blocked')) },
    })
    const element = mount(CodeBlock, { code: 'print(1)', language: 'python' })
    element.querySelector<HTMLButtonElement>('button[aria-label="rendering.wrapLines"]')!.click()
    await nextTick()
    expect(element.querySelector('.code-block--wrap')).not.toBeNull()
    expect(element.querySelector('button[aria-pressed="true"]')).not.toBeNull()
    element.querySelector<HTMLButtonElement>('button[aria-label="rendering.copyCode"]')!.click()
    await nextTick()
    await Promise.resolve()
    expect(element.querySelector('[role="status"]')?.textContent).toBe('rendering.copyFailed')
  })

  it('renders inline imports with safe syntax spans', () => {
    const element = mount(InlineCode, { code: 'import numpy' })
    expect(element.querySelector('code .hljs-keyword')?.textContent).toBe('import')
  })

  it('renders Markdown and keeps project HTML inert through the component', () => {
    const element = mount(MarkdownContent, { content: '# README\n\n<script>alert(1)</script>' })
    expect(element.querySelector('h1')?.textContent).toBe('README')
    expect(element.querySelector('script')).toBeNull()
  })

  it('allows the requested wide dialog without changing the default size', async () => {
    mount(BaseDialog, { title: 'File preview', open: true, size: 'wide' })
    await nextTick()
    expect(document.querySelector('.dialog__panel--wide[role="dialog"]')).not.toBeNull()
    application?.unmount()
    container?.remove()
    mount(BaseDialog, { title: 'Login', open: true })
    await nextTick()
    expect(document.querySelector('.dialog__panel--wide')).toBeNull()
    expect(document.querySelector('.dialog__panel[role="dialog"]')).not.toBeNull()
  })
})
