import { describe, expect, it, vi } from 'vitest'
import { createSSRApp, h, reactive, type Ref } from 'vue'
import { renderToString } from '@vue/server-renderer'
import ArchiveReview from './ArchiveReview.vue'

const { dependencies } = vi.hoisted(() => ({ dependencies: {} as Record<string, unknown> }))
vi.mock('../model/archiveReview', async (original) => ({
  readmeCommands: (await original<typeof import('../model/archiveReview')>()).readmeCommands,
  ArchiveReview: class {
    state = dependencies.state
    open = vi.fn()
    reset = vi.fn()
  },
}))
vi.mock('@/entities/project', () => ({ projectApi: {} }))
vi.mock('@/shared/i18n', () => ({ t: (key: string) => key }))
vi.mock('@/shared/ui', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    BaseCard: defineComponent({ setup: (_props, { slots }) => () => h('section', slots.default?.()) }),
    BaseButton: defineComponent({ props: ['disabled'], setup: (props, { attrs, slots }) => () => h('button', { ...attrs, type: 'button', disabled: props.disabled }, slots.default?.()) }),
    BaseInput: defineComponent({ render: () => h('input') }),
    BaseTextarea: defineComponent({ render: () => h('textarea') }),
    CodeBlock: defineComponent({ props: ['code', 'language', 'filename', 'lineNumbers'], setup: (props) => () => h('pre', { 'data-language': props.language, 'data-filename': props.filename, 'data-line-numbers': String(props.lineNumbers) }, props.code) }),
    MarkdownContent: (await import('@/shared/ui/MarkdownContent.vue')).default,
  }
})

async function render(source = false) {
  dependencies.state = reactive({
    readme: '# Actual README\n\n- Use the included CSV\n\n## Windows\n```powershell\n.\\.venv\\Scripts\\Activate.ps1\n```\n',
    readmeLoading: false, readmeError: null, verification: null, verificationLoading: false,
    verificationError: null, verificationConflict: false, saving: false, saved: false,
    form: { status: 'not_checked', pythonVersion: '', platform: '', command: '', output: '', notes: '' },
  })
  const setup = ArchiveReview.setup!
  const wrapped = {
    ...ArchiveReview,
    setup(props: Parameters<typeof setup>[0], context: Parameters<typeof setup>[1]) {
      const result = setup(props, context)
      if (source) (result as unknown as { readmeRendered: Ref<boolean> }).readmeRendered.value = false
      return result
    },
  } as typeof ArchiveReview
  const app = createSSRApp({ render: () => h(wrapped, { projectId: 'project', archiveTag: 'a'.repeat(64), canEdit: false }) })
  app.config.globalProperties.$t = (key: string) => key
  return renderToString(app)
}

describe('README instructions in the ready package', () => {
  it('renders Markdown and passes actual PowerShell commands to a code block without line numbers', async () => {
    const html = await render()
    expect(html).toContain('<h1>Actual README</h1>')
    expect(html).toContain('<ul>')
    expect(html).toContain('data-language="powershell"')
    expect(html).toContain('data-line-numbers="false"')
    expect(html).toContain('analysis.files.source')
  })

  it('shows the whole README as source when selected instead of replacing it with commands', async () => {
    const html = await render(true)
    expect(html).toContain('data-language="markdown" data-filename="README.md"')
    expect(html).toContain('# Actual README\n')
    expect(html).not.toContain('<h1>Actual README</h1>')
  })
})
