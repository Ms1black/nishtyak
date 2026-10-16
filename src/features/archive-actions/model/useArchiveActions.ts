import { reactive, ref } from 'vue'
import { getArchiveFileName, projectApi, useProjectStore, type Project } from '@/entities/project'
import { downloadBlob } from '@/shared/lib'
import { ApiError } from '@/shared/api'
import { t } from '@/shared/i18n'

export function useArchiveActions() {
  const projectStore = useProjectStore()
  const pending = reactive(new Set<string>())
  const error = ref<string | null>(null)

  async function run<T>(id: string, task: () => Promise<T>): Promise<T | null> {
    if (pending.has(id)) return null
    pending.add(id)
    error.value = null
    try {
      return await task()
    } catch (cause) {
      error.value = cause instanceof ApiError ? cause.message : t('workflow.archiveError')
      return null
    } finally {
      pending.delete(id)
    }
  }

  async function build(project: Project): Promise<Project | null> {
    projectStore.patch(project.id, { archive: { status: 'building' } })
    const updated = await run(project.id, () => projectApi.buildArchive(project.id))
    if (updated) projectStore.upsert(updated)
    else projectStore.patch(project.id, { archive: project.archive })
    return updated
  }

  async function download(project: Project) {
    const blob = await run(project.id, () => projectApi.downloadArchive(project.id))
    if (blob) downloadBlob(blob, getArchiveFileName(project))
  }

  const isPending = (id: string) => pending.has(id)

  return { build, download, isPending, error }
}
