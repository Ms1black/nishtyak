import { onScopeDispose, reactive, watch, type Ref } from 'vue'
import { projectApi, type FileContent } from '@/entities/project'
import type { ProjectApi } from '@/entities/project/api'
import { ApiError, sessionGeneration } from '@/shared/api'

interface ArchiveFileContext {
  projectId: string
  path: string
  archiveTag: string
}

export interface ArchiveFileSelection {
  projectId: string
  archiveTag?: string
  path: string | null
}

export class ArchiveFilePreview {
  readonly state = reactive({
    content: null as FileContent | null,
    isLoading: false,
    error: null as string | null,
    needsRefresh: false,
  })
  private requestId = 0
  private context: ArchiveFileContext | null = null

  constructor(private readonly api: Pick<ProjectApi, 'getArchiveFileContent'> = projectApi) {}

  async open(projectId: string, path: string, archiveTag: string) {
    this.reset()
    if (!projectId || !path || !/^[a-f0-9]{64}$/.test(archiveTag)) {
      this.state.error = 'ready.files.hashRequired'
      this.state.needsRefresh = true
      return
    }
    const requestId = this.requestId
    this.context = { projectId, path, archiveTag }
    this.state.isLoading = true
    try {
      const content = await this.api.getArchiveFileContent(projectId, path, archiveTag)
      if (requestId !== this.requestId) return
      if (content.kind !== 'binary' && content.kind !== 'hidden' &&
        !(content.kind === 'text' && typeof content.content === 'string')) {
        throw new TypeError('Unexpected file content')
      }
      this.state.content = content
    } catch (cause) {
      if (requestId !== this.requestId) return
      if (cause instanceof ApiError && cause.status === 409) {
        this.state.error = cause.code === 'ARCHIVE_INVALID' ? 'ready.files.invalid' : 'ready.files.stale'
        this.state.needsRefresh = true
      } else if (cause instanceof ApiError && cause.status === 404) {
        this.state.error = 'ready.files.missing'
        this.state.needsRefresh = true
      } else {
        this.state.error = 'ready.files.error'
      }
    } finally {
      if (requestId === this.requestId) this.state.isLoading = false
    }
  }

  retry() {
    const context = this.context
    if (context) return this.open(context.projectId, context.path, context.archiveTag)
  }

  reset() {
    this.requestId += 1
    this.context = null
    Object.assign(this.state, { content: null, isLoading: false, error: null, needsRefresh: false })
  }
}

export function useArchiveFilePreview(
  selection: Readonly<Ref<ArchiveFileSelection>>,
  open: Ref<boolean>,
  api?: Pick<ProjectApi, 'getArchiveFileContent'>,
) {
  const preview = new ArchiveFilePreview(api)
  let contextInvalidated = false
  watch([() => selection.value.projectId, () => selection.value.archiveTag], () => {
    contextInvalidated = true
    open.value = false
    preview.reset()
  }, { flush: 'sync' })
  watch([open, selection], ([isOpen, current]) => {
    if (!isOpen) {
      contextInvalidated = false
      return preview.reset()
    }
    if (contextInvalidated || !current.path) return preview.reset()
    void preview.open(current.projectId, current.path, current.archiveTag ?? '')
  }, { immediate: true, flush: 'sync' })
  watch(sessionGeneration, () => {
    contextInvalidated = true
    open.value = false
    preview.reset()
  }, { flush: 'sync' })
  onScopeDispose(() => preview.reset())
  return preview
}
