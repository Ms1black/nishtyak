import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { effectScope, nextTick, ref, type EffectScope } from 'vue'
import { resetIntegrationSession } from '@/shared/api'
import type { FileContent } from '@/entities/project/model/types'
import { useFileContent, type FileSelection } from './useFileContent'

const { getFileContent } = vi.hoisted(() => ({ getFileContent: vi.fn() }))
vi.mock('@/entities/project', () => ({ projectApi: { getFileContent } }))

let scope: EffectScope
const selection = () => ref<FileSelection>({ projectId: 'project', path: 'experiment.py', analysisId: 'analysis' })
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (value: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const settle = async () => { await Promise.resolve(); await nextTick() }

describe('read-only source request isolation', () => {
  beforeEach(() => { vi.resetAllMocks(); scope = effectScope() })
  afterEach(() => scope.stop())

  it('keeps the actual source unchanged, including whitespace and line endings', async () => {
    const source = 'import numpy as np\r\n\r\nprint("  value  ")\r\n'
    getFileContent.mockResolvedValueOnce({ kind: 'text', content: source, truncated: false })
    const state = scope.run(() => useFileContent(selection()))!
    await settle()
    expect(state.content.value).toEqual({ kind: 'text', content: source, truncated: false })
    expect(getFileContent).toHaveBeenCalledExactlyOnceWith('project', 'experiment.py')
  })

  it('rejects older project/path responses and clears previously visible content synchronously', async () => {
    const first = deferred<FileContent>()
    const second = deferred<FileContent>()
    getFileContent.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const selected = selection()
    const state = scope.run(() => useFileContent(selected))!
    selected.value = { projectId: 'other', path: 'settings.toml', analysisId: 'other-analysis' }
    second.resolve({ kind: 'text', content: 'current = true\n' })
    await settle()
    first.resolve({ kind: 'text', content: 'previous account source' })
    await settle()
    expect(state.content.value).toEqual({ kind: 'text', content: 'current = true\n' })
    selected.value = { ...selected.value, path: 'README.md' }
    expect(state.content.value).toBeNull()
  })

  it('reloads the same path after analysis changes and ignores a stale failure', async () => {
    const previous = deferred<FileContent>()
    const current = deferred<FileContent>()
    getFileContent.mockReturnValueOnce(previous.promise).mockReturnValueOnce(current.promise)
    const selected = selection()
    const state = scope.run(() => useFileContent(selected))!
    selected.value = { ...selected.value, analysisId: 'replaced-source' }
    previous.reject(new Error('Old request failed'))
    await settle()
    expect(state.error.value).toBe(false)
    expect(state.isLoading.value).toBe(true)
    current.resolve({ kind: 'text', content: 'print(5)\n' })
    await settle()
    expect(state.content.value).toEqual({ kind: 'text', content: 'print(5)\n' })
    expect(getFileContent).toHaveBeenCalledTimes(2)
  })

  it.each(['hidden', 'binary'] as const)('preserves the %s boundary without inventing preview text', async (kind) => {
    getFileContent.mockResolvedValueOnce({ kind })
    const state = scope.run(() => useFileContent(selection()))!
    await settle()
    expect(state.content.value).toEqual({ kind })
  })

  it('retries a failed file read without changing the source selection', async () => {
    getFileContent.mockRejectedValueOnce(new Error('Unavailable')).mockResolvedValueOnce({ kind: 'text', content: 'print(4)\n', truncated: true })
    const state = scope.run(() => useFileContent(selection()))!
    await settle()
    expect(state.error.value).toBe(true)
    await state.reload()
    expect(state.error.value).toBe(false)
    expect(state.content.value).toEqual({ kind: 'text', content: 'print(4)\n', truncated: true })
  })

  it('clears visible data and rejects outstanding replies after an account change', async () => {
    const pending = deferred<FileContent>()
    getFileContent.mockReturnValueOnce(pending.promise)
    const state = scope.run(() => useFileContent(selection()))!
    resetIntegrationSession()
    pending.resolve({ kind: 'text', content: 'previous account' })
    await settle()
    expect(state.content.value).toBeNull()
    expect(state.isLoading.value).toBe(false)
    expect(getFileContent).toHaveBeenCalledTimes(1)
  })

  it('does not accept a response after the file viewer is disposed', async () => {
    const pending = deferred<FileContent>()
    getFileContent.mockReturnValueOnce(pending.promise)
    const state = scope.run(() => useFileContent(selection()))!
    scope.stop()
    pending.resolve({ kind: 'text', content: 'late source' })
    await settle()
    expect(state.content.value).toBeNull()
  })
})
