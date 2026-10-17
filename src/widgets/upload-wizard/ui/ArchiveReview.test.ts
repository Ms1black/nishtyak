// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import type { VerificationDocument } from '@/entities/project/model/types'
import { ApiError } from '@/shared/api'
import ArchiveReview from './ArchiveReview.vue'

const { api } = vi.hoisted(() => ({
  api: { getReadme: vi.fn(), getVerification: vi.fn(), saveVerification: vi.fn() },
}))
vi.mock('@/entities/project', () => ({ projectApi: api }))
vi.mock('@/shared/i18n', () => ({ t: (key: string) => key }))

const TAG = 'a'.repeat(64)
const originalReadme = '# Actual README\n\n- Use the included CSV\n\n## Windows\n```powershell\n.\\.venv\\Scripts\\Activate.ps1\n```\n'
const verificationDocument = (): VerificationDocument => ({
  revision: 4, analysisId: 'analysis', archiveTag: TAG, stale: false, verification: null,
})
let application: App | undefined
let container: HTMLElement

async function flush() {
  await Promise.resolve()
  await Promise.resolve()
  await nextTick()
}

async function mount(options: { canEdit?: boolean; archiveTag?: string } = {}) {
  const onOpenFile = vi.fn()
  container = document.createElement('div')
  document.body.appendChild(container)
  application = createApp({ render: () => h(ArchiveReview, {
    projectId: 'project', archiveTag: options.archiveTag ?? TAG,
    canEdit: options.canEdit ?? true, onOpenFile,
  }) })
  application.config.globalProperties.$t = (key: string) => key
  application.mount(container)
  await flush()
  return { element: container, onOpenFile }
}

function control<T extends HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(label: string): T {
  const field = [...container.querySelectorAll('label')].find((item) => item.textContent?.includes(label))
  const input = field?.querySelector<T>('input,textarea,select')
  expect(input).toBeDefined()
  return input!
}

beforeEach(() => {
  vi.clearAllMocks()
  api.getReadme.mockResolvedValue({ content: originalReadme })
  api.getVerification.mockResolvedValue(verificationDocument())
  api.saveVerification.mockResolvedValue({ ...verificationDocument(), revision: 5 })
})
afterEach(() => {
  application?.unmount()
  container?.remove()
})

describe('compact colleague verification and shared archive preview', () => {
  it('keeps actual quick commands and opens README through one shared viewer event', async () => {
    const { element, onOpenFile } = await mount()
    expect(element.querySelector('code.language-powershell')?.textContent).toBe('.\\.venv\\Scripts\\Activate.ps1\n')
    expect(element.querySelector('.code-block__numbers')).toBeNull()
    expect(element.querySelector('.markdown-content')).toBeNull()
    expect(element.textContent).not.toContain('Actual README')
    expect(element.textContent).not.toContain('analysis.files.source')
    const button = [...element.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent === 'ready.readme.preview')!
    button.click()
    expect(onOpenFile).toHaveBeenCalledExactlyOnceWith('README.md')
  })

  it('requires the selected archive SHA before requesting its README viewer', async () => {
    const { element, onOpenFile } = await mount({ archiveTag: '' })
    const button = [...element.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent === 'ready.readme.preview')!
    expect(button.disabled).toBe(true)
    button.click()
    expect(onOpenFile).not.toHaveBeenCalled()
  })

  it('shows outcome and optional comment immediately, with blank actual environment fields under closed details', async () => {
    await mount()
    const details = container.querySelector<HTMLDetailsElement>('.archive-review__details')!
    const status = control<HTMLSelectElement>('ready.verification.status')
    const notes = control<HTMLTextAreaElement>('ready.verification.notes')
    expect(details.open).toBe(false)
    expect(status.closest('details')).toBeNull()
    expect(notes.closest('details')).toBeNull()
    expect(status.value).toBe('not_checked')
    for (const label of ['ready.verification.pythonVersion', 'ready.verification.platform', 'ready.verification.command', 'ready.verification.output']) {
      const input = control(label)
      expect(input.closest('details')).toBe(details)
      expect(input.value).toBe('')
    }
    expect(container.querySelector('button[type="submit"]')?.closest('details')).toBeNull()
  })

  it('preserves previously recorded actual versions, command and output when collapsed details are not edited', async () => {
    const previous = {
      status: 'passed' as const, pythonVersion: '3.12.8', platform: 'Windows AMD64',
      command: 'python experiment.py --input actual.csv', output: '3 measurements',
      notes: 'Original comment', checkedBy: { id: 'colleague', name: 'Colleague' }, checkedAt: '2026-10-17T08:00:00Z',
    }
    api.getVerification.mockResolvedValue({ ...verificationDocument(), verification: previous })
    await mount()
    const details = container.querySelector<HTMLDetailsElement>('details')!
    expect(details.open).toBe(false)
    expect(control<HTMLInputElement>('ready.verification.pythonVersion').value).toBe('3.12.8')
    details.open = true
    details.dispatchEvent(new Event('toggle'))
    await nextTick()
    details.open = false
    details.dispatchEvent(new Event('toggle'))
    await nextTick()
    const notes = control<HTMLTextAreaElement>('ready.verification.notes')
    notes.value = 'Only the comment changed'
    notes.dispatchEvent(new Event('input', { bubbles: true }))
    container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await flush()
    expect(api.saveVerification).toHaveBeenCalledExactlyOnceWith('project', {
      expectedRevision: 4, analysisId: 'analysis', archiveTag: TAG,
      status: 'passed', pythonVersion: previous.pythonVersion, platform: previous.platform,
      command: previous.command, output: previous.output, notes: 'Only the comment changed',
    })
  })

  it('retains read-only ownership and archive revision guards with the simpler form', async () => {
    const { element } = await mount({ canEdit: false })
    expect(element.querySelector<HTMLFieldSetElement>('fieldset')?.disabled).toBe(true)
    expect(element.querySelector('button[type="submit"]')).toBeNull()
    element.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await flush()
    expect(api.saveVerification).not.toHaveBeenCalled()
  })

  it('does not save a human result against a different archive hash', async () => {
    api.getVerification.mockResolvedValue({ ...verificationDocument(), archiveTag: 'b'.repeat(64) })
    await mount()
    container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await flush()
    expect(api.saveVerification).not.toHaveBeenCalled()
    expect(container.textContent).toContain('ready.verification.conflict')
  })

  it('keeps verification and the shared README action available when command extraction temporarily fails', async () => {
    api.getReadme.mockRejectedValue(new ApiError(503, 'Unavailable'))
    const { element, onOpenFile } = await mount()
    expect(element.textContent).toContain('ready.readme.error')
    expect(element.querySelector<HTMLFieldSetElement>('fieldset')?.disabled).toBe(false)
    const button = [...element.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent === 'ready.readme.preview')!
    button.click()
    expect(onOpenFile).toHaveBeenCalledWith('README.md')
  })
})
