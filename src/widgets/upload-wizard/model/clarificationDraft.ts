import { ref, watch, type Ref } from 'vue'
import { sessionGeneration } from '@/shared/api'
import type { AutomationQuestion } from '@/entities/project'

export interface DraftContext {
  userId: string
  projectId: string
  analysisId: string
  questionnaireId: string
}

export interface ClarificationDraft {
  values: Record<string, string>
  notes: string
}

const PREFIX = 'nishtyak:clarifications:'

function sessionStorageOrNull(): Storage | null {
  try { return typeof window === 'undefined' ? null : window.sessionStorage } catch { return null }
}

export class ClarificationDraftStorage {
  constructor(private readonly storage: () => Storage | null = sessionStorageOrNull) {}

  private key(context: DraftContext): string {
    return PREFIX + [context.userId, context.projectId, context.analysisId, context.questionnaireId]
      .map(encodeURIComponent).join(':')
  }

  load(context: DraftContext, questions: AutomationQuestion[]): ClarificationDraft | null {
    try {
      const raw = this.storage()?.getItem(this.key(context))
      if (!raw) return null
      const data = JSON.parse(raw) as Partial<ClarificationDraft>
      if (typeof data.notes !== 'string' || !data.values || typeof data.values !== 'object') return null
      const values: Record<string, string> = {}
      for (const question of questions) {
        const value = data.values[question.id]
        if (typeof value === 'string') values[question.id] = value.slice(0, 2000)
      }
      return { values, notes: data.notes.slice(0, 6000) }
    } catch { return null }
  }

  save(context: DraftContext, draft: ClarificationDraft): boolean {
    try {
      const storage = this.storage()
      if (!storage) return false
      storage.setItem(this.key(context), JSON.stringify(draft))
      return true
    } catch { return false }
  }

  remove(context: DraftContext) {
    try { this.storage()?.removeItem(this.key(context)) } catch { /* Storage can be unavailable. */ }
  }

  clear() {
    try {
      const storage = this.storage()
      if (!storage) return
      for (let index = storage.length - 1; index >= 0; index -= 1) {
        const key = storage.key(index)
        if (key?.startsWith(PREFIX)) storage.removeItem(key)
      }
    } catch { /* Logout still completes if storage is disabled. */ }
  }
}

export const clarificationDraftStorage = new ClarificationDraftStorage()
watch(sessionGeneration, () => clarificationDraftStorage.clear(), { flush: 'sync' })

export function useClarificationDraft(context: Ref<DraftContext | null>, questions: () => AutomationQuestion[]) {
  const values = ref<Record<string, string>>({})
  const notes = ref('')
  const draftSaved = ref(false)
  let loadedContext: DraftContext | null = null
  let restoring = false

  watch(context, (next) => {
    restoring = true
    loadedContext = null
    const draft = next ? clarificationDraftStorage.load(next, questions()) : null
    values.value = { ...Object.fromEntries(questions().map((question) => [question.id, ''])), ...draft?.values }
    notes.value = draft?.notes ?? ''
    draftSaved.value = !!draft
    loadedContext = next
    restoring = false
  }, { immediate: true, flush: 'sync' })

  watch([values, notes], () => {
    if (restoring || !loadedContext) return
    draftSaved.value = clarificationDraftStorage.save(loadedContext, { values: { ...values.value }, notes: notes.value })
  }, { deep: true, flush: 'sync' })

  return { values, notes, draftSaved }
}
