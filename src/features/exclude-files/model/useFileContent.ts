import { onScopeDispose, ref, watch, type Ref } from 'vue'
import { projectApi, type FileContent } from '@/entities/project'
import { sessionGeneration } from '@/shared/api'

export interface FileSelection {
  projectId: string
  path: string | null
  analysisId?: string
}

export function useFileContent(selection: Readonly<Ref<FileSelection>>) {
  const content = ref<FileContent | null>(null)
  const isLoading = ref(false)
  const error = ref(false)
  let requestId = 0

  function clear() {
    requestId += 1
    content.value = null
    isLoading.value = false
    error.value = false
  }

  async function reload() {
    clear()
    const { projectId, path } = selection.value
    if (!projectId || !path) return
    const request = requestId
    const session = sessionGeneration.value
    isLoading.value = true
    try {
      const result = await projectApi.getFileContent(projectId, path)
      if (request === requestId && session === sessionGeneration.value) content.value = result
    } catch {
      if (request === requestId && session === sessionGeneration.value) error.value = true
    } finally {
      if (request === requestId && session === sessionGeneration.value) isLoading.value = false
    }
  }

  watch(selection, () => { void reload() }, { immediate: true, flush: 'sync' })
  watch(sessionGeneration, clear, { flush: 'sync' })
  onScopeDispose(clear)

  return { content, isLoading, error, reload }
}
