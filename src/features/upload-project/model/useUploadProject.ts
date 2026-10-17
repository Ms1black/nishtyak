import { computed, onScopeDispose, ref, watch } from 'vue'
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
  const workspaceStatus = ref<'loading' | 'ready' | 'error'>('loading')
  const workspaceError = ref<string | null>(null)
  const canUpload = computed(() =>
    workspaceStatus.value === 'ready' && !!teamStore.currentTeam &&
    teamStore.canEditProjects && !!file.value && !isUploading.value,
  )
  let workspaceRequest = 0

  async function loadWorkspace() {
    const request = ++workspaceRequest
    const generation = sessionGeneration.value
    workspaceStatus.value = 'loading'
    workspaceError.value = null
    if (error.value?.startsWith('upload.workspace.')) error.value = null
    try {
      await teamStore.init()
      if (request !== workspaceRequest || generation !== sessionGeneration.value) return
      if (!teamStore.currentTeam) throw new Error('Workspace is not available')
      workspaceStatus.value = 'ready'
    } catch {
      if (request !== workspaceRequest || generation !== sessionGeneration.value) return
      workspaceStatus.value = 'error'
      workspaceError.value = 'upload.workspace.error'
    }
  }

  watch(sessionGeneration, () => {
    file.value = null
    error.value = null
    void loadWorkspace()
  }, { immediate: true })
  onScopeDispose(() => { workspaceRequest += 1 })

  function select(files: File[] | null) {
    error.value = null
    if (!files?.length) {
      file.value = null
      return
    }
    if (files.length !== 1) {
      error.value = 'upload.errors.multiple'
      file.value = null
      return
    }
    const next = files.at(0)!
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
    if (isUploading.value) return null
    if (!file.value) {
      error.value = 'upload.errors.required'
      return null
    }
    if (workspaceStatus.value !== 'ready' || !teamId || !teamStore.currentTeam) {
      error.value = workspaceStatus.value === 'loading' ? 'upload.workspace.loading' : 'upload.workspace.error'
      return null
    }
    if (!teamStore.canEditProjects) {
      error.value = 'teams.readOnly'
      return null
    }

    const generation = sessionGeneration.value
    const request = workspaceRequest
    isUploading.value = true
    error.value = null
    try {
      const project = await projectApi.upload(teamId, file.value)
      if (request !== workspaceRequest || generation !== sessionGeneration.value || teamId !== teamStore.currentTeamId) return null
      projectStore.upsert(project)
      file.value = null
      return project
    } catch (cause) {
      if (request !== workspaceRequest || generation !== sessionGeneration.value || teamId !== teamStore.currentTeamId) return null
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

  return { file, error, isUploading, canUpload, workspaceStatus, workspaceError, loadWorkspace, select, upload }
}
