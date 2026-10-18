import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { computed, effectScope, nextTick, reactive, ref, type EffectScope } from 'vue'
import type { FileContent } from '@/entities/project/model/types'
import { ApiError, resetIntegrationSession } from '@/shared/api'
import { ArchiveFilePreview, useArchiveFilePreview } from './archiveFilePreview'

vi.mock('@/entities/project', () => ({ projectApi: {} }))
const SHA = 'a'.repeat(64)
const NEW_SHA = 'b'.repeat(64)

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (cause: unknown) => void
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}

describe('actual built ZIP preview', () => {
  const api = { getArchiveFileContent: vi.fn() }
  let scope: EffectScope
  beforeEach(() => { vi.resetAllMocks(); scope = effectScope() })
  afterEach(() => scope.stop())

  it('passes exact generated/source paths and SHA without replacing data with original source', async () => {
    const original = '  print("<not html>")\r\n'
    api.getArchiveFileContent.mockResolvedValue({ kind: 'text', content: original })
    const preview = new ArchiveFilePreview(api)
    await preview.open('project', 'project/lab experiment.py', SHA)
    expect(api.getArchiveFileContent).toHaveBeenCalledExactlyOnceWith('project', 'project/lab experiment.py', SHA)
    expect(preview.state.content).toEqual({ kind: 'text', content: original })
    await preview.open('project', 'README.md', SHA)
    expect(api.getArchiveFileContent).toHaveBeenLastCalledWith('project', 'README.md', SHA)
  })

  it('does not fetch without a valid archive hash', async () => {
    const preview = new ArchiveFilePreview(api)
    await preview.open('project', 'README.md', '')
    expect(api.getArchiveFileContent).not.toHaveBeenCalled()
    expect(preview.state.error).toBe('ready.files.hashRequired')
    expect(preview.state.needsRefresh).toBe(true)
  })

  it('clears old content synchronously and ignores a late response for another file', async () => {
    const pending = deferred<FileContent>()
    api.getArchiveFileContent.mockReturnValueOnce(pending.promise)
    api.getArchiveFileContent.mockResolvedValueOnce({ kind: 'text', content: '# Generated README' })
    const preview = new ArchiveFilePreview(api)
    const first = preview.open('project', 'project/experiment.py', SHA)
    await preview.open('project', 'README.md', SHA)
    pending.resolve({ kind: 'text', content: 'Old source content' })
    await first
    expect(preview.state.content).toEqual({ kind: 'text', content: '# Generated README' })
  })

  it('ignores a late failure after changing the project or archive SHA', async () => {
    const pending = deferred<FileContent>()
    api.getArchiveFileContent.mockReturnValueOnce(pending.promise)
    api.getArchiveFileContent.mockResolvedValueOnce({ kind: 'text', content: 'Current archive' })
    const preview = new ArchiveFilePreview(api)
    const old = preview.open('old-project', 'README.md', SHA)
    await preview.open('new-project', 'README.md', NEW_SHA)
    pending.reject(new ApiError(409, 'Old ZIP', 'ARCHIVE_STALE'))
    await old
    expect(preview.state.content).toEqual({ kind: 'text', content: 'Current archive' })
    expect(preview.state.error).toBeNull()
  })

  it('retries transient failures against the same ZIP identity', async () => {
    api.getArchiveFileContent.mockRejectedValueOnce(new ApiError(503, 'Unavailable'))
    api.getArchiveFileContent.mockResolvedValueOnce({ kind: 'text', content: '{}' })
    const preview = new ArchiveFilePreview(api)
    await preview.open('project', 'analysis-report.json', SHA)
    expect(preview.state.error).toBe('ready.files.error')
    expect(preview.state.needsRefresh).toBe(false)
    await preview.retry()
    expect(preview.state.error).toBeNull()
    expect(api.getArchiveFileContent).toHaveBeenLastCalledWith('project', 'analysis-report.json', SHA)
  })

  it.each([[409, 'ARCHIVE_STALE', 'ready.files.stale'], [409, 'ARCHIVE_NOT_BUILT', 'ready.files.stale'], [409, 'ARCHIVE_INVALID', 'ready.files.invalid'], [404, 'FILE_NOT_FOUND', 'ready.files.missing']])('requires metadata refresh after HTTP %s %s', async (status, code, message) => {
    api.getArchiveFileContent.mockRejectedValueOnce(new ApiError(Number(status), 'Unavailable', String(code)))
    const preview = new ArchiveFilePreview(api)
    await preview.open('project', 'README.md', SHA)
    expect(preview.state.error).toBe(message)
    expect(preview.state.needsRefresh).toBe(true)
    expect(preview.state.content).toBeNull()
  })

  it.each([{ kind: 'hidden' }, { kind: 'binary' }, { kind: 'text', content: 'partial\n', truncated: true }])('preserves %s content handling from the archive endpoint', async (content) => {
    api.getArchiveFileContent.mockResolvedValueOnce(content)
    const preview = new ArchiveFilePreview(api)
    await preview.open('project', 'project/data.bin', SHA)
    expect(preview.state.content).toEqual(content)
  })

  it('closes and clears the viewer on a SHA change, project change, logout and scope disposal', async () => {
    api.getArchiveFileContent.mockResolvedValue({ kind: 'text', content: 'Private content' })
    const selected = reactive({ projectId: 'project', archiveTag: SHA, path: 'README.md' })
    const open = ref(true)
    const preview = scope.run(() => useArchiveFilePreview(computed(() => ({ ...selected })), open, api))!
    await nextTick()
    expect(preview.state.content?.kind).toBe('text')
    selected.archiveTag = NEW_SHA
    expect(open.value).toBe(false)
    expect(preview.state.content).toBeNull()
    expect(api.getArchiveFileContent).toHaveBeenCalledOnce()
    open.value = true
    await nextTick()
    expect(api.getArchiveFileContent).toHaveBeenLastCalledWith('project', 'README.md', NEW_SHA)
    selected.projectId = 'other'
    expect(open.value).toBe(false)
    expect(preview.state.content).toBeNull()
    open.value = true
    await nextTick()
    resetIntegrationSession()
    expect(open.value).toBe(false)
    expect(preview.state.content).toBeNull()
    scope.stop()
    expect(preview.state.content).toBeNull()
  })

  it('does not fetch another ZIP while a v-model close is waiting for the parent update', async () => {
    api.getArchiveFileContent.mockResolvedValue({ kind: 'text', content: 'Current ZIP' })
    const selected = ref({ projectId: 'project', archiveTag: SHA, path: 'README.md' })
    const parentOpen = ref(true)
    const requestClose = vi.fn()
    const delayedModel = computed({ get: () => parentOpen.value, set: requestClose })
    scope.run(() => useArchiveFilePreview(selected, delayedModel, api))
    await nextTick()
    selected.value = { ...selected.value, projectId: 'new-project', archiveTag: NEW_SHA }
    expect(requestClose).toHaveBeenCalledWith(false)
    expect(api.getArchiveFileContent).toHaveBeenCalledOnce()
    parentOpen.value = false
    parentOpen.value = true
    await nextTick()
    expect(api.getArchiveFileContent).toHaveBeenLastCalledWith('new-project', 'README.md', NEW_SHA)
  })
})
