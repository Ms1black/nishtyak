import { afterEach, describe, expect, it, vi } from 'vitest'
import { computed, effectScope, reactive } from 'vue'
import { ClarificationDraftStorage, clarificationDraftStorage, useClarificationDraft } from './clarificationDraft'
import { resetIntegrationSession } from '@/shared/api'

class MemoryStorage implements Storage {
  private data = new Map<string, string>()
  get length() { return this.data.size }
  key(index: number) { return [...this.data.keys()][index] ?? null }
  getItem(key: string) { return this.data.get(key) ?? null }
  setItem(key: string, value: string) { this.data.set(key, value) }
  removeItem(key: string) { this.data.delete(key) }
  clear() { this.data.clear() }
}

const context = { userId: 'user', projectId: 'project', analysisId: 'analysis', questionnaireId: 'batch' }
const questions = [{ id: 'package', aspect: 'dependencies' as const, kind: 'package_mapping' as const, module: 'custom' }]

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('clarification session draft', () => {
  it('restores the current questionnaire only and ignores unknown answer fields', () => {
    const storage = new ClarificationDraftStorage(() => new MemoryStorage())
    const memory = new MemoryStorage()
    const drafts = new ClarificationDraftStorage(() => memory)
    expect(storage.load(context, questions)).toBeNull()
    drafts.save(context, { values: { package: 'numpy', foreign: 'private' }, notes: 'Known input data' })
    expect(drafts.load(context, questions)).toEqual({ values: { package: 'numpy' }, notes: 'Known input data' })
    expect(drafts.load({ ...context, projectId: 'other' }, questions)).toBeNull()
    expect(drafts.load({ ...context, analysisId: 'other' }, questions)).toBeNull()
    expect(drafts.load({ ...context, questionnaireId: 'other' }, questions)).toBeNull()
    expect(drafts.load({ ...context, userId: 'other' }, questions)).toBeNull()
  })

  it('does not fail the questionnaire when session storage is disabled', () => {
    const drafts = new ClarificationDraftStorage(() => { throw new Error('Storage denied') })
    expect(drafts.save(context, { values: {}, notes: 'Keep the form working' })).toBe(false)
    expect(drafts.load(context, questions)).toBeNull()
    expect(() => drafts.clear()).not.toThrow()
  })

  it('removes only the submitted questionnaire', () => {
    const memory = new MemoryStorage()
    const drafts = new ClarificationDraftStorage(() => memory)
    drafts.save(context, { values: {}, notes: 'Submitted' })
    drafts.save({ ...context, projectId: 'other' }, { values: {}, notes: 'Keep me' })
    drafts.remove(context)
    expect(drafts.load(context, questions)).toBeNull()
    expect(drafts.load({ ...context, projectId: 'other' }, questions)?.notes).toBe('Keep me')
  })

  it('does not leak a previous project answer when input and context change in the same tick', () => {
    const memory = new MemoryStorage()
    vi.stubGlobal('window', { sessionStorage: memory })
    const scope = effectScope()
    const selected = reactive({ ...context })
    const form = scope.run(() => useClarificationDraft(computed(() => ({ ...selected })), () => questions))!
    form.notes.value = 'Private project A'
    form.values.value.package = 'numpy'
    selected.projectId = 'B'
    expect(form.notes.value).toBe('')
    expect(clarificationDraftStorage.load({ ...context, projectId: 'B' }, questions)).toBeNull()
    selected.projectId = 'project'
    expect(form.notes.value).toBe('Private project A')
    expect(form.values.value.package).toBe('numpy')
    scope.stop()
  })

  it('clears session drafts on account reset and leaves unrelated session data intact', () => {
    const memory = new MemoryStorage()
    vi.stubGlobal('window', { sessionStorage: memory })
    memory.setItem('unrelated', 'keep')
    clarificationDraftStorage.save(context, { values: {}, notes: 'Private answer' })
    resetIntegrationSession()
    expect(clarificationDraftStorage.load(context, questions)).toBeNull()
    expect(memory.getItem('unrelated')).toBe('keep')
  })
})
