import { defineStore } from 'pinia'
import { computed, ref, toRaw, watch } from 'vue'
import {
  createDefaultDecisions,
  projectApi,
  useProjectStore,
  type AnalysisDecisions,
  type AnalysisOptions,
  type DependencyLock,
  type PackagePlan,
  type Project,
  type ProjectAnalysis,
  type ProjectAutomation,
  type RunSpec,
  type TargetPlatform,
} from '@/entities/project'
import { ApiError, sessionGeneration } from '@/shared/api'
import { useTeamStore } from '@/entities/team'
import { debounce } from '@/shared/lib'
import { mergeDecisionChanges, type ClarificationAnswers } from './clarifications'

export type WizardStep = 1 | 2 | 3
export type SaveState = 'idle' | 'saving' | 'saved' | 'error' | 'conflict'

const POLL_INTERVAL_MS = 2000
const SYNC_DELAY_MS = 500

export const useUploadWizardStore = defineStore('upload-wizard', () => {
  const teamStore = useTeamStore()
  const canEdit = computed(() => teamStore.canEditProjects)
  const projectStore = useProjectStore()
  const step = ref<WizardStep>(1)
  const project = ref<Project | null>(null)
  const analysis = ref<ProjectAnalysis | null>(null)
  const decisions = ref<AnalysisDecisions | null>(null)
  const revision = ref(0)
  const plan = ref<PackagePlan | null>(null)
  const lastAutomation = ref<ProjectAutomation | null>(null)
  const lock = ref<DependencyLock | null>(null)
  const includeDockerfile = ref(false)
  const isLoading = ref(false)
  const isBuilding = ref(false)
  const isResolving = ref(false)
  const isRefreshing = ref(false)
  const error = ref<string | null>(null)
  const saveState = ref<SaveState>('idle')
  const savedAt = ref<Date | null>(null)
  const advancedOpen = ref(false)

  let pollTimer: ReturnType<typeof setTimeout> | undefined
  let resolutionTimer: ReturnType<typeof setTimeout> | undefined
  let epoch = 0
  let editVersion = 0
  let savedVersion = -1
  let autoBuildRequested = false
  let syncing: { epoch: number; promise: Promise<boolean> } | null = null
  let submittedQuestionnaire: string | null = null

  const isAnalyzing = computed(() => analysis.value?.status === 'analyzing')
  const automation = computed(() => plan.value?.automation ?? lastAutomation.value ?? analysis.value?.automation ?? null)
  const isAutomating = computed(() =>
    ['static', 'assisting', 'validating', 'finalizing'].includes(automation.value?.phase ?? ''),
  )
  const isPreparingEnvironment = computed(() =>
    automation.value
      ? isAutomating.value && automation.value.aspect === 'environment'
      : ['pending', 'running'].includes(plan.value?.automaticResolution?.status ?? ''),
  )
  const isBusy = computed(
    () =>
      isBuilding.value || isResolving.value || isRefreshing.value ||
      isLoading.value || isAnalyzing.value || isPreparingEnvironment.value || isAutomating.value,
  )
  const canBuild = computed(() =>
    canEdit.value && !!plan.value && plan.value.state !== 'blocked' &&
    automation.value?.phase !== 'needs_input' && !isBusy.value,
  )
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

  async function openProject(next: Project, options: { autoBuild?: boolean } = {}) {
    reset()
    autoBuildRequested = options.autoBuild === true && canEdit.value && next.archive.status !== 'ready'
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
      project.value = { ...project.value!, analysis: result.status, filesCount: result.files.length }
      projectStore.upsert(project.value)
      if (result.status === 'failed') return

      const doc = await projectApi.getDecisions(projectId)
      if (epoch !== requestEpoch) return
      revision.value = doc.revision
      const hasSavedDecisions = doc.decisions?.analysisId === result.analysisId
      decisions.value = hasSavedDecisions ? doc.decisions : createDefaultDecisions(result)
      savedVersion = hasSavedDecisions ? editVersion : -1
      if (
        !hasSavedDecisions && autoBuildRequested && canEdit.value &&
        decisions.value!.excludedFiles.length > 0
      ) {
        const saved = await projectApi.saveDecisions(projectId, snapshotDecisions(), revision.value)
        if (epoch !== requestEpoch) return
        revision.value = saved.revision
        savedVersion = editVersion
      }
      const nextPlan = await projectApi.plan(projectId, snapshotDecisions())
      if (epoch !== requestEpoch) return
      plan.value = nextPlan
      scheduleResolutionPoll()
      const existingLock = await projectApi.getLock(projectId).catch(() => null)
      if (epoch !== requestEpoch) return
      const automatic = nextPlan.automaticResolution
      const matchesAutomatic = !automatic || automatic.status === 'disabled' || automatic.lockId === existingLock?.id
      if (existingLock?.analysis_id === result.analysisId && matchesAutomatic) lock.value = existingLock
    } catch (cause) {
      if (epoch === requestEpoch) error.value = message(cause, 'analysis.errors.load')
    } finally {
      if (epoch === requestEpoch) {
        isLoading.value = false
        await continueAutomaticBuild()
      }
    }
  }

  function scheduleResolutionPoll() {
    clearTimeout(resolutionTimer)
    if (isPreparingEnvironment.value || isAutomating.value) {
      resolutionTimer = setTimeout(() => void pollAutomaticResolution(), POLL_INTERVAL_MS)
    }
  }

  async function pollAutomaticResolution() {
    if (!project.value || !decisions.value || (!isPreparingEnvironment.value && !isAutomating.value)) return
    const requestEpoch = epoch
    const version = editVersion
    try {
      const nextPlan = await projectApi.plan(project.value.id, snapshotDecisions())
      if (epoch !== requestEpoch || version !== editVersion) return
      plan.value = nextPlan
      if (nextPlan.automaticResolution?.status === 'ready' && lock.value?.id !== nextPlan.automaticResolution.lockId) {
        lock.value = null
      }
      error.value = null
    } catch (cause) {
      if (epoch !== requestEpoch) return
      error.value = message(cause, 'analysis.errors.load')
    }
    scheduleResolutionPoll()
    await continueAutomaticBuild()
  }

  async function continueAutomaticBuild() {
    if (!autoBuildRequested || !plan.value) return
    if (plan.value.state === 'blocked') return
    if (automation.value?.phase === 'needs_input') return
    if (!automation.value && plan.value.automaticResolution?.status === 'needs_input') return
    if (isBusy.value || error.value) return
    autoBuildRequested = false
    await build()
  }

  function snapshotDecisions(): AnalysisDecisions {
    return JSON.parse(JSON.stringify(toRaw(decisions.value!))) as AnalysisDecisions
  }

  async function sync(): Promise<boolean> {
    if (!canEdit.value || !project.value || !decisions.value || automation.value?.phase === 'needs_input') return false
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
          scheduleResolutionPoll()
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
    const saved = await promise
    if (epoch === requestEpoch) await continueAutomaticBuild()
    return saved
  }

  const scheduleSync = debounce(() => void sync(), SYNC_DELAY_MS)

  function update(changes: Partial<AnalysisDecisions>) {
    if (!canEdit.value || !decisions.value || isBusy.value) return
    const waitingForBatch = automation.value?.phase === 'needs_input'
    decisions.value = { ...decisions.value, ...changes }
    editVersion += 1
    plan.value = null
    lock.value = null
    invalidateArchive()
    saveState.value = waitingForBatch ? 'idle' : 'saving'
    if (!waitingForBatch) scheduleSync()
  }

  async function answerQuestions(answer: ClarificationAnswers) {
    const current = automation.value
    const questionnaireId = current?.questionnaireId
    if (
      !canEdit.value || !project.value || !decisions.value || isBusy.value ||
      current?.phase !== 'needs_input' || !questionnaireId || submittedQuestionnaire === questionnaireId ||
      current.humanRound === 1 || current.stage === 'post_human'
    ) return
    autoBuildRequested = true
    scheduleSync.cancel()
    const requestEpoch = epoch
    const projectId = project.value.id
    isRefreshing.value = true
    saveState.value = 'saving'
    try {
      if (syncing?.epoch === requestEpoch) await syncing.promise
      if (epoch !== requestEpoch || automation.value?.questionnaireId !== questionnaireId) return
      const snapshot = mergeDecisionChanges(snapshotDecisions(), answer.changes)
      const doc = await projectApi.submitClarifications(projectId, {
        questionnaireId,
        expectedRevision: revision.value,
        decisions: snapshot,
        notes: answer.notes.trim(),
      })
      if (epoch !== requestEpoch) return
      submittedQuestionnaire = questionnaireId
      decisions.value = doc.decisions ?? snapshot
      revision.value = doc.revision
      editVersion += 1
      savedVersion = editVersion
      lock.value = null
      invalidateArchive()
      const finalizing: ProjectAutomation = {
        ...current, phase: 'finalizing', stage: 'post_human', humanRound: 1,
        questions: [], question: null,
      }
      lastAutomation.value = finalizing
      plan.value = plan.value ? { ...plan.value, automation: finalizing } : null
      saveState.value = 'saved'
      savedAt.value = new Date()
      error.value = null
      const nextPlan = await projectApi.plan(projectId, snapshotDecisions())
      if (epoch !== requestEpoch) return
      plan.value = nextPlan
    } catch (cause) {
      if (epoch === requestEpoch) {
        const committed = submittedQuestionnaire === questionnaireId
        if (!committed) saveState.value = cause instanceof ApiError && cause.status === 409 ? 'conflict' : 'error'
        error.value = message(cause, committed ? 'analysis.errors.load' : 'analysis.panel.saveError')
      }
    } finally {
      if (epoch === requestEpoch) {
        isRefreshing.value = false
        scheduleResolutionPoll()
        await continueAutomaticBuild()
      }
    }
  }

  async function reloadDecisions() {
    if (!canEdit.value || !project.value || isBusy.value) return
    await openProject(project.value)
  }

  async function refreshPlan() {
    if (!canEdit.value || !project.value || !decisions.value || isBusy.value || automation.value?.phase === 'needs_input') return
    const requestEpoch = epoch
    scheduleSync.cancel()
    isRefreshing.value = true
    try {
      await sync()
    } finally {
      if (epoch === requestEpoch) {
        isRefreshing.value = false
        await continueAutomaticBuild()
      }
    }
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
    if (!canEdit.value || !project.value || !decisions.value || isBusy.value || automation.value?.phase === 'needs_input') return
    autoBuildRequested = false
    const requestEpoch = epoch
    const projectId = project.value.id
    isBuilding.value = true
    error.value = null
    scheduleSync.cancel()
    try {
      if (
        (savedVersion !== editVersion || saveState.value === 'error' || saveState.value === 'conflict') &&
        !(await sync())
      ) return
      if (epoch !== requestEpoch) return
      if (!plan.value || plan.value.state === 'blocked' || plan.value.automation?.phase === 'needs_input') return
      if (isPreparingEnvironment.value || isAutomating.value) {
        autoBuildRequested = true
        return
      }
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
    if (!canEdit.value || !project.value || isBusy.value) return
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
    if (!canEdit.value || !project.value || !decisions.value || isBusy.value || automation.value?.phase === 'needs_input') return
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

  function backToAnalysis() {
    if (project.value) {
      advancedOpen.value = true
      step.value = 2
    }
  }

  function reset() {
    epoch += 1
    clearTimeout(pollTimer)
    clearTimeout(resolutionTimer)
    scheduleSync.cancel()
    syncing = null
    submittedQuestionnaire = null
    editVersion = 0
    savedVersion = -1
    autoBuildRequested = false
    step.value = 1
    project.value = null
    analysis.value = null
    decisions.value = null
    plan.value = null
    lastAutomation.value = null
    lock.value = null
    revision.value = 0
    includeDockerfile.value = false
    isLoading.value = false
    isBuilding.value = false
    isResolving.value = false
    isRefreshing.value = false
    error.value = null
    saveState.value = 'idle'
    savedAt.value = null
    advancedOpen.value = false
  }

  function message(cause: unknown, fallback: string): string {
    return cause instanceof ApiError ? cause.message : fallback
  }

  watch(sessionGeneration, reset, { flush: 'sync' })
  watch(() => teamStore.currentTeamId, reset, { flush: 'sync' })
  watch(() => plan.value?.automation, (value) => {
    if (value) lastAutomation.value = value
  }, { flush: 'sync' })

  return {
    canEdit,
    step,
    project,
    analysis,
    decisions,
    plan,
    lock,
    includeDockerfile,
    isLoading,
    isBuilding,
    isResolving,
    isRefreshing,
    isAnalyzing,
    isPreparingEnvironment,
    automation,
    isAutomating,
    isBusy,
    error,
    saveState,
    savedAt,
    advancedOpen,
    canBuild,
    canIncludeDockerfile,
    openProject,
    loadAnalysis,
    update,
    answerQuestions,
    reloadDecisions,
    refreshPlan,
    setPackage,
    setVersion,
    toggleModule,
    toggleFile,
    toggleExtra,
    setRunSpec,
    build,
    reanalyze,
    resolve,
    backToAnalysis,
    reset,
  }
})
