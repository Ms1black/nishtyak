import { ref } from 'vue'
import { projectApi, useProjectStore, type Project } from '@/entities/project'
import { useTeamStore } from '@/entities/team'
import { validateProjectFile } from '../lib/validateProjectFile'
import { ApiError, sessionGeneration } from '@/shared/api'

export function useUploadProject() {
  const teamStore = useTeamStore()
  const projectStore = useProjectStore()

  const file = ref<File | null>(null)
  const error = ref<string | null>(null)
  const isUploading = ref(false)

  function select(next: File | null) {
    error.value = null
    if (!next) {
      file.value = null
      return
    }
    const validationError = validateProjectFile(next)
    if (validationError) {
      error.value = validationError
      file.value = null
      return
    }
    file.value = next
  }

  async function upload(): Promise<Project | null> {
    const teamId = teamStore.currentTeamId
    if (!file.value || !teamId || !teamStore.canEditProjects || isUploading.value) return null

    const generation = sessionGeneration.value
    isUploading.value = true
    error.value = null
    try {
      const project = await projectApi.upload(teamId, file.value)
      if (generation !== sessionGeneration.value || teamId !== teamStore.currentTeamId) return null
      projectStore.upsert(project)
      file.value = null
      return project
    } catch (cause) {
      if (generation !== sessionGeneration.value || teamId !== teamStore.currentTeamId) return null
      error.value =
        cause instanceof ApiError
          ? cause.code === 'OWNER_STORAGE_LIMIT'
            ? 'workflow.ownerStorageLimit'
            : cause.message
          : 'upload.errors.upload'
      return null
    } finally {
      isUploading.value = false
    }
  }

  return { file, error, isUploading, select, upload }
}
