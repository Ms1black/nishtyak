import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { projectApi, useProjectStore } from '@/entities/project'
import { useTeamStore } from '@/entities/team'
import { useUploadWizardStore } from '@/widgets/upload-wizard'
import { ApiError, sessionGeneration } from '@/shared/api'

export function useProjectRoute() {
  const route = useRoute()
  const router = useRouter()
  const wizard = useUploadWizardStore()
  const projectStore = useProjectStore()
  const teamStore = useTeamStore()
  const isOpening = ref(false)
  const openError = ref<string | null>(null)
  const retryAttempt = ref(0)
  let cancelOpening: (() => void) | null = null
  let initializingTeam = false
  let switchingTeam = false

  function replaceProject(id: string | null) {
    const currentId = typeof route.query.project === 'string' ? route.query.project : null
    if (currentId === id) return
    const query = { ...route.query }
    if (id) query.project = id
    else delete query.project
    void router.replace({ query })
  }

  watch(
    () => [route.query.project, retryAttempt.value, sessionGeneration.value] as const,
    async ([value], _previous, onCleanup) => {
      let cancelled = false
      onCleanup(() => { cancelled = true })
      initializingTeam = false
      switchingTeam = false
      cancelOpening = () => {
        cancelled = true
        isOpening.value = false
        openError.value = 'upload.open.workspaceChanged'
      }
      const id = typeof value === 'string' ? value : null
      openError.value = null
      if (!id) {
        isOpening.value = false
        if (wizard.project) wizard.reset()
        return
      }
      if (wizard.project?.id === id) return
      isOpening.value = true
      if (wizard.project) wizard.reset()
      try {
        initializingTeam = teamStore.teams.length === 0
        await teamStore.init()
        if (cancelled) return
        initializingTeam = false
        const result = await projectApi.get(id)
        if (cancelled) return
        if (!teamStore.teams.some((team) => team.id === result.teamId)) {
          await teamStore.refresh()
          if (cancelled) return
        }
        if (cancelled) return
        if (!teamStore.teams.some((team) => team.id === result.teamId)) {
          throw new ApiError(403, 'Project workspace is not accessible')
        }
        switchingTeam = true
        teamStore.switchTeam(result.teamId)
        switchingTeam = false
        projectStore.upsert(result.project)
        await wizard.openProject(result.project)
      } catch (cause) {
        if (cancelled) return
        openError.value = cause instanceof ApiError && cause.status === 403
          ? 'upload.open.forbidden'
          : cause instanceof ApiError && cause.status === 404
            ? 'upload.open.missing'
            : 'upload.open.error'
      } finally {
        if (!cancelled) {
          isOpening.value = false
          initializingTeam = false
          switchingTeam = false
        }
      }
    },
    { immediate: true },
  )

  watch(() => wizard.project?.id ?? null, (id) => {
    if (!isOpening.value && !openError.value) replaceProject(id)
  })
  watch(() => teamStore.currentTeamId, () => {
    if (isOpening.value && !initializingTeam && !switchingTeam) cancelOpening?.()
  }, { flush: 'sync' })

  function newProject() {
    openError.value = null
    wizard.reset()
    replaceProject(null)
  }

  return { isOpening, openError, retry: () => { retryAttempt.value += 1 }, newProject }
}
