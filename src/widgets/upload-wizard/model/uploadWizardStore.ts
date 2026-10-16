import { defineStore } from 'pinia'
import { computed, ref, toRaw } from 'vue'
import {
  createDefaultDecisions,
  projectApi,
  useProjectStore,
  type AdviceFeature,
  type AdviceSuggestion,
  type AnalysisDecisions,
  type AnalysisOptions,
  type DependencyLock,
  type PackagePlan,
  type Project,
  type ProjectAdvice,
  type ProjectAnalysis,
  type RunSpec,
  type TargetPlatform,
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
  const lock = ref<DependencyLock | null>(null)
  const advice = ref<ProjectAdvice | null>(null)
  const includeDockerfile = ref(false)
  const isLoading = ref(false)
  const isBuilding = ref(false)
  const isResolving = ref(false)
  const isAdvising = ref(false)
  const error = ref<string | null>(null)
  const saveState = ref<SaveState>('idle')
  const savedAt = ref<Date | null>(null)

  let pollTimer: ReturnType<typeof setTimeout> | undefined
  let epoch = 0
  let editVersion = 0
  let savedVersion = -1
  let syncing: { epoch: number; promise: Promise<boolean> } | null = null

  const isAnalyzing = computed(() => analysis.value?.status === 'analyzing')
  const isBusy = computed(() => isBuilding.value || isResolving.value || isLoading.value || isAnalyzing.value)
  const canBuild = computed(() => !!plan.value && plan.value.state !== 'blocked' && !isBusy.value)
  const canIncludeDockerfile = computed(() => {
    const current = plan.value
    if (current?.state !== 'ready_to_verify' || !current.pythonVersion || !current.runSpec) return false
    if (!/^[A-Za-z0-9_./-]+$/.test(current.runSpec.workingDirectory)) return false
    if (current.requirements.some((requirement) => /\s@\s/.test(requirement))) return false
    return !analysis.value?.files.some((file) => /(^|\/)environment\.ya?ml$/.test(file.path))
  })

  function invalidateArchive() {
    if (!project.value) return
    project.value = { ...project.value, archive: { status: 'not_built' } }
    projectStore.upsert(project.value)
  }

  async function openProject(next: Project) {
    reset()
    project.value = next
    step.value = next.archive.status === 'ready' ? 3 : 2
    await loadAnalysis()
  }

  async function loadAnalysis() {
    if (!project.value) return
    clearTimeout(pollTimer)
    const requestEpoch = epoch
    const projectId = project.value.id
    isLoading.value = !analysis.value
    error.value = null
    try {
      const result = await projectApi.getAnalysis(projectId)
      if (epoch !== requestEpoch) return
      analysis.value = result
      if (result.status === 'analyzing') {
        pollTimer = setTimeout(() => void loadAnalysis(), POLL_INTERVAL_MS)
        return
      }
      project.value = { ...project.value!, analysis: result.status }
      projectStore.upsert(project.value)
      if (result.status === 'failed') return

      const doc = await projectApi.getDecisions(projectId)
      if (epoch !== requestEpoch) return
      revision.value = doc.revision
      decisions.value = doc.decisions?.analysisId === result.analysisId ? doc.decisions : createDefaultDecisions(result)
      savedVersion = doc.decisions?.analysisId === result.analysisId ? editVersion : -1
      const nextPlan = await projectApi.plan(projectId, snapshotDecisions())
      if (epoch !== requestEpoch) return
      plan.value = nextPlan
      const existingLock = await projectApi.getLock(projectId).catch(() => null)
      if (epoch !== requestEpoch) return
      if (existingLock?.analysis_id === result.analysisId) lock.value = existingLock
    } catch (cause) {
      if (epoch === requestEpoch) error.value = message(cause, 'analysis.errors.load')
    } finally {
      if (epoch === requestEpoch) isLoading.value = false
    }
  }

  function snapshotDecisions(): AnalysisDecisions {
    return JSON.parse(JSON.stringify(toRaw(decisions.value!))) as AnalysisDecisions
  }

  async function sync(): Promise<boolean> {
    if (!project.value || !decisions.value) return false
    if (syncing?.epoch === epoch) return syncing.promise
    const requestEpoch = epoch
    const projectId = project.value.id
    saveState.value = 'saving'
    const promise = (async () => {
      try {
        do {
          const version = editVersion
          const snapshot = snapshotDecisions()
          const doc = await projectApi.saveDecisions(projectId, snapshot, revision.value)
          if (epoch !== requestEpoch) return false
          revision.value = doc.revision
          savedVersion = version
          const nextPlan = await projectApi.plan(projectId, snapshot)
          if (epoch !== requestEpoch) return false
          if (version === editVersion) plan.value = nextPlan
        } while (savedVersion !== editVersion)
        saveState.value = 'saved'
        savedAt.value = new Date()
        error.value = null
        return true
      } catch (cause) {
        if (epoch === requestEpoch) {
          saveState.value = cause instanceof ApiError && cause.status === 409 ? 'conflict' : 'error'
          error.value = message(cause, 'analysis.panel.saveError')
        }
        return false
      } finally {
        if (syncing?.epoch === requestEpoch) syncing = null
      }
    })()
    syncing = { epoch: requestEpoch, promise }
    return promise
  }

  const scheduleSync = debounce(() => void sync(), SYNC_DELAY_MS)

  function update(changes: Partial<AnalysisDecisions>) {
    if (!decisions.value || isBusy.value) return
    decisions.value = { ...decisions.value, ...changes }
    editVersion += 1
    plan.value = null
    lock.value = null
    invalidateArchive()
    saveState.value = 'saving'
    scheduleSync()
  }

  async function reloadDecisions() {
    if (!project.value || isBusy.value) return
    await openProject(project.value)
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

  function toggleInList(key: 'excludedModules' | 'excludedFiles' | 'selectedExtras', value: string) {
    if (!decisions.value) return
    const list = decisions.value[key]
    update({ [key]: list.includes(value) ? list.filter((item) => item !== value) : [...list, value] })
  }

  const toggleModule = (module: string) => toggleInList('excludedModules', module)
  const toggleFile = (path: string) => toggleInList('excludedFiles', path)
  const toggleExtra = (group: string) => toggleInList('selectedExtras', group)
  const setRunSpec = (runSpec: RunSpec | null) => update({ runSpec })

  async function build() {
    if (!project.value || !decisions.value || isBusy.value) return
    const requestEpoch = epoch
    const projectId = project.value.id
    isBuilding.value = true
    error.value = null
    scheduleSync.cancel()
    try {
      if (!(await sync()) || epoch !== requestEpoch) return
      if (!plan.value || plan.value.state === 'blocked') return
      if (includeDockerfile.value && !canIncludeDockerfile.value) {
        error.value = 'workflow.dockerUnavailable'
        return
      }
      projectStore.patch(projectId, { archive: { status: 'building' } })
      const built = await projectApi.buildArchive(projectId, {
        includeDockerfile: includeDockerfile.value,
        ...(lock.value ? { lockId: lock.value.id } : {}),
      })
      if (epoch !== requestEpoch) return
      projectStore.upsert(built)
      project.value = built
      step.value = 3
    } catch (cause) {
      if (epoch === requestEpoch) {
        error.value = message(cause, 'analysis.errors.build')
        invalidateArchive()
      }
    } finally {
      if (epoch === requestEpoch) isBuilding.value = false
    }
  }

  async function reanalyze(options: AnalysisOptions, authorEnvironment?: unknown) {
    if (!project.value || isBusy.value) return
    const requestEpoch = epoch
    isLoading.value = true
    error.value = null
    scheduleSync.cancel()
    try {
      if (syncing?.epoch === epoch) await syncing.promise
      if (epoch !== requestEpoch) return
      const updated = await projectApi.reanalyze(project.value.id, options, authorEnvironment)
      if (epoch !== requestEpoch) return
      projectStore.upsert(updated)
      await openProject(updated)
    } catch (cause) {
      if (epoch === requestEpoch) error.value = message(cause, 'analysis.errors.load')
    } finally {
      if (epoch === requestEpoch) isLoading.value = false
    }
  }

  async function resolve(target: { pythonVersion: string; platform: TargetPlatform }) {
    if (!project.value || !decisions.value || isBusy.value) return
    const requestEpoch = epoch
    isResolving.value = true
    error.value = null
    scheduleSync.cancel()
    try {
      if (!(await sync()) || epoch !== requestEpoch) return
      const result = await projectApi.resolve(project.value.id, target, snapshotDecisions())
      if (epoch !== requestEpoch) return
      lock.value = result
      invalidateArchive()
    } catch (cause) {
      if (epoch === requestEpoch) error.value = message(cause, 'workflow.resolveError')
    } finally {
      if (epoch === requestEpoch) isResolving.value = false
    }
  }

  async function requestAdvice(feature: AdviceFeature, description: string) {
    if (!project.value || !analysis.value || isAdvising.value || isBusy.value) return
    const requestEpoch = epoch
    const analysisId = analysis.value.analysisId
    isAdvising.value = true
    advice.value = null
    error.value = null
    try {
      const result = await projectApi.advice(project.value.id, feature, description)
      if (epoch === requestEpoch && result.analysis_id === analysisId) advice.value = result
    } catch (cause) {
      if (epoch === requestEpoch) error.value = message(cause, 'workflow.adviceUnavailable')
    } finally {
      if (epoch === requestEpoch) isAdvising.value = false
    }
  }

  function applySuggestion(suggestion: AdviceSuggestion) {
    if (!decisions.value || advice.value?.analysis_id !== decisions.value.analysisId) return
    if (suggestion.kind === 'readme_description') {
      update({ description: [decisions.value.description, suggestion.text].filter(Boolean).join('\n\n') })
    } else if (suggestion.kind === 'package_hypothesis' && suggestion.module && suggestion.package) {
      setPackage(suggestion.module, suggestion.package)
    } else if (suggestion.kind === 'entrypoint_hypothesis') {
      const candidate = analysis.value?.launchCandidates?.find((item) => item.id === suggestion.candidate_id)
      if (candidate) setRunSpec(candidate.runSpec)
    }
  }

  function backToAnalysis() {
    if (project.value) step.value = 2
  }

  function reset() {
    epoch += 1
    clearTimeout(pollTimer)
    scheduleSync.cancel()
    syncing = null
    editVersion = 0
    savedVersion = -1
    step.value = 1
    project.value = null
    analysis.value = null
    decisions.value = null
    plan.value = null
    lock.value = null
    advice.value = null
    revision.value = 0
    includeDockerfile.value = false
    isLoading.value = false
    isBuilding.value = false
    isResolving.value = false
    isAdvising.value = false
    error.value = null
    saveState.value = 'idle'
    savedAt.value = null
  }

  function message(cause: unknown, fallback: string): string {
    return cause instanceof ApiError ? cause.message : fallback
  }

  return {
    step,
    project,
    analysis,
    decisions,
    plan,
    lock,
    advice,
    includeDockerfile,
    isLoading,
    isBuilding,
    isResolving,
    isAdvising,
    isAnalyzing,
    isBusy,
    error,
    saveState,
    savedAt,
    canBuild,
    canIncludeDockerfile,
    openProject,
    loadAnalysis,
    update,
    reloadDecisions,
    setPackage,
    setVersion,
    toggleModule,
    toggleFile,
    toggleExtra,
    setRunSpec,
    build,
    reanalyze,
    resolve,
    requestAdvice,
    applySuggestion,
    backToAnalysis,
    reset,
  }
})
