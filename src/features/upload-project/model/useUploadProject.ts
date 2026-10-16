import { ref } from 'vue'
import { projectApi, useProjectStore, type Project } from '@/entities/project'
import { useTeamStore } from '@/entities/team'
import { validateProjectFile } from '../lib/validateProjectFile'

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
    if (!file.value || !teamId) return null

    isUploading.value = true
    error.value = null
    try {
      const project = await projectApi.upload(teamId, file.value)
      projectStore.upsert(project)
      file.value = null
      return project
    } catch {
      error.value = 'upload.errors.upload'
      return null
    } finally {
      isUploading.value = false
    }
  }

  return { file, error, isUploading, select, upload }
}
