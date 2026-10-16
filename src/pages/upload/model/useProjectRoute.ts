import { watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useProjectStore } from '@/entities/project'
import { useTeamStore } from '@/entities/team'
import { useUploadWizardStore } from '@/widgets/upload-wizard'

export function useProjectRoute() {
  const route = useRoute()
  const router = useRouter()
  const wizard = useUploadWizardStore()
  const projectStore = useProjectStore()
  const teamStore = useTeamStore()

  function replaceProject(id: string | null) {
    const currentId = typeof route.query.project === 'string' ? route.query.project : null
    if (currentId === id) return
    const query = { ...route.query }
    if (id) query.project = id
    else delete query.project
    void router.replace({ query })
  }

  watch(
    () => route.query.project,
    async (value, _previous, onCleanup) => {
      let cancelled = false
      onCleanup(() => { cancelled = true })
      const id = typeof value === 'string' ? value : null
      if (!id) {
        if (wizard.project) wizard.reset()
        return
      }
      if (wizard.project?.id === id) return
      await teamStore.init()
      if (cancelled) return
      if (!projectStore.getById(id) && teamStore.currentTeamId) {
        await projectStore.fetchAll(teamStore.currentTeamId)
      }
      if (cancelled) return
      const project = projectStore.getById(id)
      if (project) await wizard.openProject(project)
      else {
        wizard.reset()
        replaceProject(null)
      }
    },
    { immediate: true },
  )

  watch(() => wizard.project?.id ?? null, (id) => replaceProject(id))
}
