import { defineStore } from 'pinia'
import { computed, ref, toRaw } from 'vue'
import {
  createDefaultDecisions,
  projectApi,
  useProjectStore,
  type AnalysisDecisions,
  type PackagePlan,
  type Project,
  type ProjectAnalysis,
  type RunSpec,
} from '@/entities/project'
import { ApiError } from '@/shared/api'
import { debounce } from '@/shared/lib'

export type WizardStep = 1 | 2 | 3
export type SaveState = 'idle' | 'saving' | 'saved' | 'error' | 'conflict'

const POLL_INTERVAL_MS = 2000
const SYNC_DELAY_MS = 500

export const useUploadWizardStore = defineStore('upload-wizard', () => {
  const projectStore = useProjectStore()

  const step = ref<WizardStep>(1)
  const project = ref<Project | null>(null)
  const analysis = ref<ProjectAnalysis | null>(null)
  const decisions = ref<AnalysisDecisions | null>(null)
  const revision = ref(0)
  const plan = ref<PackagePlan | null>(null)

  const isLoading = ref(false)
  const isBuilding = ref(false)
  const error = ref<string | null>(null)
  const saveState = ref<SaveState>('idle')
  const savedAt = ref<Date | null>(null)

  let pollTimer: ReturnType<typeof setTimeout> | undefined
  let syncing: Promise<void> | null = null
  let dirty = false

  const isAnalyzing = computed(() => analysis.value?.status === 'analyzing')
  const canBuild = computed(() => !!plan.value && plan.value.state !== 'blocked' && !isBuilding.value)

  async function openProject(next: Project) {
    reset()
    project.value = next
    step.value = next.archive.status === 'ready' ? 3 : 2
    await loadAnalysis()
  }

  async function loadAnalysis() {
    if (!project.value) return
    const projectId = project.value.id
    isLoading.value = !analysis.value
    error.value = null
    try {
      const result = await projectApi.getAnalysis(projectId)
      if (project.value?.id !== projectId) return
      analysis.value = result
      if (result.status === 'analyzing') {
        pollTimer = setTimeout(loadAnalysis, POLL_INTERVAL_MS)
        return
      }
      if (result.status === 'failed') return
      if (project.value.analysis !== result.status) {
        project.value = { ...project.value, analysis: result.status }
        projectStore.patch(projectId, { analysis: result.status, archive: { status: 'not_built' } })
      }
      const doc = await projectApi.getDecisions(projectId)
      revision.value = doc.revision
      decisions.value = doc.decisions?.analysisId === result.analysisId ? doc.decisions : createDefaultDecisions(result)
      plan.value = await projectApi.plan(projectId, decisions.value)
    } catch {
      error.value = 'analysis.errors.load'
    } finally {
      isLoading.value = false
    }
  }

  async function sync() {
    if (!project.value || !decisions.value) return
    if (syncing) {
      dirty = true
      return syncing
    }
    const projectId = project.value.id
    saveState.value = 'saving'
    syncing = (async () => {
      try {
        do {
          dirty = false
          const snapshot: AnalysisDecisions = JSON.parse(JSON.stringify(toRaw(decisions.value!)))
          const doc = await projectApi.saveDecisions(projectId, snapshot, revision.value)
          revision.value = doc.revision
          plan.value = await projectApi.plan(projectId, snapshot)
        } while (dirty)
        saveState.value = 'saved'
        savedAt.value = new Date()
      } catch (e) {
        saveState.value = e instanceof ApiError && e.status === 409 ? 'conflict' : 'error'
      } finally {
        syncing = null
      }
    })()
    return syncing
  }

  const scheduleSync = debounce(() => void sync(), SYNC_DELAY_MS)

  function update(changes: Partial<AnalysisDecisions>) {
    if (!decisions.value) return
    decisions.value = { ...decisions.value, ...changes }
    saveState.value = 'saving'
    scheduleSync()
  }

  async function reloadDecisions() {
    scheduleSync.cancel()
    saveState.value = 'idle'
    analysis.value = null
    await loadAnalysis()
  }

  function setMapValue(key: 'packages' | 'versions', module: string, value: string) {
    if (!decisions.value) return
    const next = { ...decisions.value[key] }
    if (value) next[module] = value
    else delete next[module]
    update({ [key]: next })
  }

  const setPackage = (module: string, value: string) => setMapValue('packages', module, value)
  const setVersion = (module: string, value: string) => setMapValue('versions', module, value)

  function toggleInList(key: 'excludedModules' | 'excludedFiles', value: string) {
    if (!decisions.value) return
    const list = decisions.value[key]
    update({ [key]: list.includes(value) ? list.filter((v) => v !== value) : [...list, value] })
  }

  const toggleModule = (module: string) => toggleInList('excludedModules', module)
  const toggleFile = (path: string) => toggleInList('excludedFiles', path)
  const setRunSpec = (runSpec: RunSpec | null) => update({ runSpec })

  async function build() {
    if (!project.value || !decisions.value) return
    isBuilding.value = true
    error.value = null
    scheduleSync.cancel()
    try {
      await sync()
      if (saveState.value === 'conflict' || saveState.value === 'error') throw new Error('save failed')
      if (plan.value?.state === 'blocked') return
      projectStore.patch(project.value.id, { archive: { status: 'building' } })
      const built = await projectApi.buildArchive(project.value.id)
      projectStore.upsert(built)
      project.value = built
      step.value = 3
    } catch {
      error.value = 'analysis.errors.build'
      if (project.value) projectStore.patch(project.value.id, { archive: { status: 'not_built' } })
    } finally {
      isBuilding.value = false
    }
  }

  function backToAnalysis() {
    if (project.value) step.value = 2
  }

  function reset() {
    clearTimeout(pollTimer)
    scheduleSync.cancel()
    step.value = 1
    project.value = null
    analysis.value = null
    decisions.value = null
    plan.value = null
    revision.value = 0
    error.value = null
    saveState.value = 'idle'
    savedAt.value = null
  }

  return {
    step,
    project,
    analysis,
    decisions,
    plan,
    isLoading,
    isBuilding,
    isAnalyzing,
    error,
    saveState,
    savedAt,
    canBuild,
    openProject,
    update,
    reloadDecisions,
    setPackage,
    setVersion,
    toggleModule,
    toggleFile,
    setRunSpec,
    build,
    backToAnalysis,
    reset,
  }
})
