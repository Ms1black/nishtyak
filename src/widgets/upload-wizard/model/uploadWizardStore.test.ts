import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import type { AnalysisDecisions, PackagePlan, Project, ProjectAnalysis } from '@/entities/project/model/types'
import { ApiError, resetIntegrationSession } from '@/shared/api'
import { useUploadWizardStore } from './uploadWizardStore'

const { api, projects, team } = vi.hoisted(() => ({
  team: { canEditProjects: true },
  api: {
    getAnalysis: vi.fn(),
    getDecisions: vi.fn(),
    saveDecisions: vi.fn(),
    submitClarifications: vi.fn(),
    plan: vi.fn(),
    getLock: vi.fn(),
    buildArchive: vi.fn(),
    reanalyze: vi.fn(),
    resolve: vi.fn(),
  },
  projects: { upsert: vi.fn(), patch: vi.fn() },
}))

vi.mock('@/entities/project', async () => ({
  createDefaultDecisions: (await import('@/entities/project/lib/decisions')).createDefaultDecisions,
  projectApi: api,
  useProjectStore: () => projects,
}))
vi.mock('@/entities/team', () => ({ useTeamStore: () => team }))
vi.mock('@/shared/lib', async () => ({ debounce: (await import('@/shared/lib/debounce')).debounce }))

function project(id: string): Project {
  return {
    id,
    name: id,
    sourceType: 'py',
    sizeBytes: 40,
    filesCount: 1,
    analysis: 'full',
    archive: { status: 'not_built' },
    updatedAt: '2026-10-16T12:00:00Z',
  }
}

function analysis(id: string): ProjectAnalysis {
  return {
    analysisId: `analysis-${id}`,
    projectId: id,
    status: 'full',
    files: [{ path: 'experiment.py', size: 40 }],
    dependencies: [],
    entrypoints: [],
    pythonRequires: null,
    findings: [],
    suggestedRunSpec: { kind: 'script', target: 'experiment.py', args: [], workingDirectory: '.' },
  }
}

function plan(decisions: AnalysisDecisions): PackagePlan {
  return {
    state: 'draft',
    requirements: [],
    findings: [],
    runSpec: decisions.runSpec ?? {
      kind: 'script',
      target: 'experiment.py',
      args: [],
      workingDirectory: '.',
    },
    decisions,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

function batchAutomation(): PackagePlan['automation'] {
  return {
    phase: 'needs_input', stage: 'pre_human', humanRound: 0,
    questionnaireId: 'questionnaire-batch',
    questions: [
      { id: 'first', aspect: 'dependencies', kind: 'package_mapping', module: 'first' },
      { id: 'second', aspect: 'dependencies', kind: 'package_mapping', module: 'second' },
    ],
  }
}

describe('upload wizard request coordination', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.resetAllMocks()
    team.canEditProjects = true
    setActivePinia(createPinia())
    api.getAnalysis.mockImplementation(async (id: string) => analysis(id))
    api.getDecisions.mockResolvedValue({ revision: 0, decisions: null, updatedAt: null })
    api.getLock.mockRejectedValue(new ApiError(404, 'No lock'))
    api.plan.mockImplementation(async (_id: string, decisions: AnalysisDecisions) => plan(decisions))
    api.saveDecisions.mockImplementation(
      async (_id: string, decisions: AnalysisDecisions, revision: number) => ({
        revision: revision + 1,
        decisions,
        updatedAt: '2026-10-16T12:00:00Z',
      }),
    )
    api.submitClarifications.mockImplementation(async (_id: string, answers: { decisions: AnalysisDecisions; expectedRevision: number }) => ({
      revision: answers.expectedRevision + 1,
      decisions: answers.decisions,
      updatedAt: '2026-10-16T12:00:00Z',
    }))
    api.buildArchive.mockImplementation(async (id: string) => ({
      ...project(id),
      archive: { status: 'ready', sizeBytes: 500 },
    }))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('restores a saved archive only after loading its complete plan and decisions', async () => {
    const stored = project('saved-archive')
    stored.archive = { status: 'ready', files: ['README.md', 'requirements.txt'], sizeBytes: 500 }
    const pending = deferred<PackagePlan>()
    api.plan.mockReturnValueOnce(pending.promise)
    const store = useUploadWizardStore()
    const opening = store.openProject(stored)
    await vi.waitFor(() => expect(api.plan).toHaveBeenCalledOnce())
    expect(store.isLoading).toBe(true)
    expect(store.plan).toBeNull()
    pending.resolve({
      ...plan(store.decisions!), requirements: ['numpy'], pythonVersion: '3.11',
      automation: { phase: 'draft', stage: 'complete', humanRound: 1 },
    })
    await opening
    expect(store.plan?.requirements).toEqual(['numpy'])
    expect(store.plan?.runSpec?.target).toBe('experiment.py')
    expect(store.plan?.pythonVersion).toBe('3.11')
    expect(store.plan?.automation?.phase).toBe('draft')
    expect(store.isLoading).toBe(false)
    expect(store.step).toBe(3)
    expect(api.saveDecisions).not.toHaveBeenCalled()
    expect(api.submitClarifications).not.toHaveBeenCalled()
    expect(api.buildArchive).not.toHaveBeenCalled()
  })

  it('recovers from a 503 while restoring archive metadata without editing or rebuilding the archive', async () => {
    const stored = project('retry-saved-archive')
    stored.archive = { status: 'ready', sizeBytes: 500 }
    api.plan.mockRejectedValueOnce(new ApiError(503, 'Plan temporarily unavailable'))
    const store = useUploadWizardStore()
    await store.openProject(stored)
    expect(store.error).toBe('Plan temporarily unavailable')
    expect(store.plan).toBeNull()
    const pending = deferred<PackagePlan>()
    api.plan.mockReturnValueOnce(pending.promise)
    const retrying = store.loadAnalysis()
    await vi.waitFor(() => expect(api.plan).toHaveBeenCalledTimes(2))
    expect(store.isLoading).toBe(true)
    expect(store.error).toBeNull()
    pending.resolve({ ...plan(store.decisions!), requirements: ['numpy'] })
    await retrying
    expect(store.error).toBeNull()
    expect(store.isLoading).toBe(false)
    expect(store.plan?.requirements).toEqual(['numpy'])
    expect(store.project?.archive.status).toBe('ready')
    expect(api.saveDecisions).not.toHaveBeenCalled()
    expect(api.submitClarifications).not.toHaveBeenCalled()
    expect(api.buildArchive).not.toHaveBeenCalled()
  })

  it('ignores a previous project document arriving after switching projects', async () => {
    const oldDocument = deferred<{ revision: number; decisions: null; updatedAt: null }>()
    api.getDecisions.mockImplementation((id: string) =>
      id === 'old' ? oldDocument.promise : Promise.resolve({ revision: 3, decisions: null, updatedAt: null }),
    )
    const store = useUploadWizardStore()
    const openingOld = store.openProject(project('old'))
    await Promise.resolve()
    await store.openProject(project('new'))
    oldDocument.resolve({ revision: 77, decisions: null, updatedAt: null })
    await openingOld
    expect(store.project?.id).toBe('new')
    expect(store.decisions?.analysisId).toBe('analysis-new')
    expect(api.plan.mock.calls.map((call) => call[0])).toEqual(['new'])
  })

  it('saves an edit made during an outstanding save before allowing a build', async () => {
    const store = useUploadWizardStore()
    await store.openProject(project('example'))
    const firstSave = deferred<{ revision: number; decisions: AnalysisDecisions; updatedAt: null }>()
    api.saveDecisions.mockImplementationOnce(() => firstSave.promise)
    store.update({ description: 'first' })
    await vi.advanceTimersByTimeAsync(500)
    store.update({ description: 'latest' })
    const building = store.build()
    expect(api.buildArchive).not.toHaveBeenCalled()
    const firstSnapshot = api.saveDecisions.mock.calls[0]![1] as AnalysisDecisions
    firstSave.resolve({ revision: 1, decisions: firstSnapshot, updatedAt: null })
    await building
    expect(api.saveDecisions).toHaveBeenCalledTimes(2)
    expect(api.saveDecisions.mock.calls[1]![1].description).toBe('latest')
    expect(api.saveDecisions.mock.calls[1]![2]).toBe(1)
    expect(store.plan?.decisions?.description).toBe('latest')
    expect(api.buildArchive).toHaveBeenCalledTimes(1)
    expect(store.step).toBe(3)
  })

  it('does not let an old save replace the new project plan', async () => {
    const store = useUploadWizardStore()
    await store.openProject(project('old'))
    const oldSave = deferred<{ revision: number; decisions: AnalysisDecisions; updatedAt: null }>()
    api.saveDecisions.mockImplementationOnce(() => oldSave.promise)
    store.update({ description: 'old project edit' })
    await vi.advanceTimersByTimeAsync(500)
    const oldSnapshot = api.saveDecisions.mock.calls[0]![1] as AnalysisDecisions
    await store.openProject(project('new'))
    oldSave.resolve({ revision: 42, decisions: oldSnapshot, updatedAt: null })
    await Promise.resolve()
    await Promise.resolve()
    expect(store.decisions?.analysisId).toBe('analysis-new')
    expect(store.plan?.decisions?.analysisId).toBe('analysis-new')
    expect(store.saveState).toBe('idle')
  })

  it('stops export after a revision conflict and keeps the local edit', async () => {
    const store = useUploadWizardStore()
    await store.openProject(project('example'))
    store.update({ description: 'unsaved local edit' })
    api.saveDecisions.mockRejectedValueOnce(new ApiError(409, 'Decisions changed'))
    await store.build()
    expect(store.saveState).toBe('conflict')
    expect(store.decisions?.description).toBe('unsaved local edit')
    expect(api.buildArchive).not.toHaveBeenCalled()
    expect(store.error).toBe('Decisions changed')
  })

  it('uses the inferred launch without requiring a manually selected main file', async () => {
    const store = useUploadWizardStore()
    await store.openProject(project('example'))
    expect(store.decisions?.runSpec).toBeNull()
    expect(store.plan?.runSpec?.target).toBe('experiment.py')
    await store.build()
    expect(api.buildArchive).toHaveBeenCalledOnce()
    expect(api.saveDecisions.mock.calls[0]![1].runSpec).toBeNull()
    expect(store.plan?.state).toBe('draft')
    expect(store.step).toBe(3)
  })

  it('prepares a new upload through archive download without environment questions or manual resolution', async () => {
    api.plan.mockImplementation(async (_id: string, decisions: AnalysisDecisions) => ({
      ...plan(decisions),
      pythonVersion: '3.11',
      pythonVersionOrigin: 'inferred',
      requirements: ['numpy', 'pandas'],
      automaticResolution: {
        status: 'ready',
        target: { pythonVersion: '3.11', platform: 'linux-x86_64' },
        packages: ['numpy==2.2.6', 'pandas==2.3.0', 'python-dateutil==2.9.0.post0'],
        provenance: 'newly_resolved_not_author_environment',
      },
    }))
    const store = useUploadWizardStore()
    await store.openProject(project('new-upload'), { autoBuild: true })
    expect(store.step).toBe(3)
    expect(api.resolve).not.toHaveBeenCalled()
    expect(api.buildArchive).toHaveBeenCalledExactlyOnceWith('new-upload', { includeDockerfile: false })
    expect(api.saveDecisions.mock.calls[0]![1]).toMatchObject({
      pythonVersion: null,
      runSpec: null,
      versions: {},
      dataConfirmed: false,
    })
    expect(store.plan?.automaticResolution?.packages).toContain('numpy==2.2.6')
  })

  it('saves automatic secret exclusions before requesting environment preparation for a new upload', async () => {
    api.getAnalysis.mockImplementation(async (id: string) => ({
      ...analysis(id),
      files: [{ path: 'experiment.py', size: 40 }, { path: '.env', size: 50, secret: 'api_token' }],
    }))
    let excludedOnServer = false
    api.saveDecisions.mockImplementation(async (_id: string, decisions: AnalysisDecisions, revision: number) => {
      excludedOnServer = decisions.excludedFiles.includes('.env')
      return { revision: revision + 1, decisions, updatedAt: null }
    })
    api.plan.mockImplementation(async (_id: string, decisions: AnalysisDecisions) => ({
      ...plan(decisions),
      automaticResolution: {
        status: excludedOnServer ? 'ready' : 'needs_input',
        reason: excludedOnServer ? undefined : 'DECISIONS_NOT_SAVED',
        packages: [],
      },
    }))
    const store = useUploadWizardStore()
    await store.openProject(project('secret-upload'), { autoBuild: true })
    expect(api.saveDecisions.mock.invocationCallOrder[0]).toBeLessThan(api.plan.mock.invocationCallOrder[0]!)
    expect(api.saveDecisions.mock.calls[0]![1]).toMatchObject({ excludedFiles: ['.env'], dataConfirmed: false })
    expect(store.plan?.automaticResolution?.status).toBe('ready')
    expect(store.step).toBe(3)
    expect(api.buildArchive).toHaveBeenCalledOnce()
  })

  it.each([
    { label: 'a reopened project', viewer: false, autoBuild: false },
    { label: 'a viewer session', viewer: true, autoBuild: true },
  ])('does not persist initial secret exclusions for $label', async ({ viewer, autoBuild }) => {
    team.canEditProjects = !viewer
    api.getAnalysis.mockImplementation(async (id: string) => ({
      ...analysis(id), files: [{ path: '.env', size: 50, secret: 'api_token' }],
    }))
    const store = useUploadWizardStore()
    await store.openProject(project('existing-secret'), { autoBuild })
    expect(store.decisions?.excludedFiles).toEqual(['.env'])
    expect(api.saveDecisions).not.toHaveBeenCalled()
    expect(api.buildArchive).not.toHaveBeenCalled()
  })

  it('does not start an automatic exclusions save after the session changed during initial loading', async () => {
    api.getAnalysis.mockImplementation(async (id: string) => ({
      ...analysis(id), files: [{ path: '.env', size: 50, secret: 'api_token' }],
    }))
    const document = deferred<{ revision: number; decisions: null; updatedAt: null }>()
    api.getDecisions.mockReturnValueOnce(document.promise)
    const store = useUploadWizardStore()
    const opening = store.openProject(project('old-secret'), { autoBuild: true })
    await Promise.resolve()
    resetIntegrationSession()
    document.resolve({ revision: 0, decisions: null, updatedAt: null })
    await opening
    expect(store.project).toBeNull()
    expect(api.saveDecisions).not.toHaveBeenCalled()
    expect(api.plan).not.toHaveBeenCalled()
  })

  it('ignores a late initial exclusions save response after the session changed', async () => {
    api.getAnalysis.mockImplementation(async (id: string) => ({
      ...analysis(id), files: [{ path: '.env', size: 50, secret: 'api_token' }],
    }))
    const saved = deferred<{ revision: number; decisions: AnalysisDecisions; updatedAt: null }>()
    api.saveDecisions.mockReturnValueOnce(saved.promise)
    const store = useUploadWizardStore()
    const opening = store.openProject(project('old-secret'), { autoBuild: true })
    await Promise.resolve()
    await Promise.resolve()
    expect(api.saveDecisions).toHaveBeenCalledOnce()
    const snapshot = api.saveDecisions.mock.calls[0]![1] as AnalysisDecisions
    resetIntegrationSession()
    saved.resolve({ revision: 7, decisions: snapshot, updatedAt: null })
    await opening
    expect(store.project).toBeNull()
    expect(store.decisions).toBeNull()
    expect(api.plan).not.toHaveBeenCalled()
    expect(api.buildArchive).not.toHaveBeenCalled()
  })

  it('waits for automatic version resolution and then builds the uploaded project', async () => {
    api.getLock.mockResolvedValueOnce({
      id: 'obsolete-lock', analysis_id: 'analysis-new-upload', packages: ['numpy==1.0.0'],
      target: { python_version: '3.10', platform: 'linux-x86_64' },
    })
    api.plan.mockImplementation(async (_id: string, decisions: AnalysisDecisions) => ({
      ...plan(decisions),
      automaticResolution: { status: 'ready', packages: ['numpy==2.2.6'] },
    }))
    api.plan.mockImplementationOnce(async (_id: string, decisions: AnalysisDecisions) => ({
      ...plan(decisions),
      automaticResolution: { status: 'running' },
    }))
    const store = useUploadWizardStore()
    await store.openProject(project('new-upload'), { autoBuild: true })
    expect(store.isPreparingEnvironment).toBe(true)
    expect(store.lock).toBeNull()
    expect(store.canBuild).toBe(false)
    expect(api.buildArchive).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(2000)
    expect(store.isPreparingEnvironment).toBe(false)
    expect(api.buildArchive).toHaveBeenCalledOnce()
    expect(api.buildArchive).toHaveBeenCalledWith('new-upload', { includeDockerfile: false })
    expect(store.step).toBe(3)
  })

  it('never automatically rebuilds an existing project when opening its page', async () => {
    const store = useUploadWizardStore()
    await store.openProject(project('existing'))
    expect(store.step).toBe(2)
    expect(api.saveDecisions).not.toHaveBeenCalled()
    expect(api.buildArchive).not.toHaveBeenCalled()
  })

  it('replaces the initial queued file count with the analyzed project inventory', async () => {
    api.getAnalysis.mockImplementation(async (id: string) => ({
      ...analysis(id), files: [
        { path: 'experiment.py', size: 40 }, { path: 'calculations.py', size: 80 }, { path: 'plot.py', size: 30 },
      ],
    }))
    const store = useUploadWizardStore()
    await store.openProject({ ...project('uploaded'), filesCount: 0 })
    expect(store.project?.filesCount).toBe(3)
    expect(projects.upsert.mock.lastCall?.[0].filesCount).toBe(3)
  })

  it('pauses at genuine ambiguity and permits an explicit draft export', async () => {
    api.plan.mockImplementation(async (_id: string, decisions: AnalysisDecisions) => ({
      ...plan(decisions),
      automaticResolution: { status: 'needs_input', reason: 'dependencies_unresolved' },
    }))
    const store = useUploadWizardStore()
    await store.openProject(project('ambiguous'), { autoBuild: true })
    expect(store.step).toBe(2)
    expect(api.buildArchive).not.toHaveBeenCalled()
    expect(store.canBuild).toBe(true)
    await store.build()
    expect(api.buildArchive).toHaveBeenCalledOnce()
    expect(store.step).toBe(3)
  })

  it('waits for the server assistance cycle before showing a terminal question or building', async () => {
    api.plan.mockImplementation(async (_id: string, decisions: AnalysisDecisions) => ({
      ...plan(decisions),
      automation: { phase: 'ready' },
      automaticResolution: { status: 'ready', packages: [] },
    }))
    api.plan.mockImplementationOnce(async (_id: string, decisions: AnalysisDecisions) => ({
      ...plan(decisions),
      automation: { phase: 'assisting', aspect: 'dependencies', attempt: 1, maxAttempts: 2 },
      automaticResolution: { status: 'needs_input', reason: 'DEPENDENCY_UNRESOLVED' },
    }))
    const store = useUploadWizardStore()
    await store.openProject(project('assisting'), { autoBuild: true })
    expect(store.automation?.phase).toBe('assisting')
    expect(store.isBusy).toBe(true)
    expect(store.canBuild).toBe(false)
    expect(store.automation?.question).toBeUndefined()
    await store.answerQuestions({ changes: { packages: { custom: 'requests' } }, notes: '' })
    expect(api.saveDecisions).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(2000)
    expect(store.step).toBe(3)
    expect(api.buildArchive).toHaveBeenCalledOnce()
  })

  it('uses a server terminal question even when an older nested resolution is still running', async () => {
    api.plan.mockImplementation(async (_id: string, decisions: AnalysisDecisions) => ({
      ...plan(decisions),
      automation: {
        phase: 'needs_input', aspect: 'dependencies', attempt: 2, maxAttempts: 2,
        question: { id: 'package-custom', aspect: 'dependencies', kind: 'package_mapping', module: 'custom' },
      },
      automaticResolution: { status: 'running' },
    }))
    const store = useUploadWizardStore()
    await store.openProject(project('question'), { autoBuild: true })
    expect(store.isBusy).toBe(false)
    expect(store.automation?.question?.id).toBe('package-custom')
    expect(store.canBuild).toBe(false)
    await store.build()
    expect(api.saveDecisions).not.toHaveBeenCalled()
    expect(api.buildArchive).not.toHaveBeenCalled()
  })

  it('submits the entire questionnaire once and waits for the final pass before building', async () => {
    api.plan.mockImplementation(async (_id: string, decisions: AnalysisDecisions) => ({
      ...plan(decisions),
      automation: api.submitClarifications.mock.calls.length ? {
        phase: 'finalizing', stage: 'post_human', humanRound: 1, finalAttempt: 1, questions: [],
      } : batchAutomation(),
    }))
    const store = useUploadWizardStore()
    await store.openProject(project('batch'), { autoBuild: true })
    store.update({ packages: { retained: 'pandas' }, versions: { retained: '>=2' } })
    await vi.advanceTimersByTimeAsync(500)
    expect(api.saveDecisions).not.toHaveBeenCalled()
    await store.answerQuestions({
      changes: {
        packages: { first: 'requests', second: 'numpy' },
        versions: { second: '>=1.26' },
        declarationActions: { declared: { action: 'replace', requirement: 'numpy>=1.26' } },
      }, notes: 'Both scripts use the same input table.',
    })
    expect(api.submitClarifications).toHaveBeenCalledOnce()
    const submitted = api.submitClarifications.mock.calls[0]![1]
    expect(submitted.questionnaireId).toBe('questionnaire-batch')
    expect(submitted.decisions.packages).toEqual({ retained: 'pandas', first: 'requests', second: 'numpy' })
    expect(submitted.decisions.versions).toEqual({ retained: '>=2', second: '>=1.26' })
    expect(submitted.notes).toContain('same input table')
    expect(store.automation?.phase).toBe('finalizing')
    expect(store.canBuild).toBe(false)
    await store.answerQuestions({ changes: {}, notes: '' })
    expect(api.submitClarifications).toHaveBeenCalledOnce()
    expect(api.buildArchive).not.toHaveBeenCalled()
    api.plan.mockImplementation(async (_id: string, decisions: AnalysisDecisions) => ({
      ...plan(decisions), automation: { phase: 'ready', stage: 'complete', humanRound: 1, questions: [] },
    }))
    await vi.advanceTimersByTimeAsync(2000)
    expect(api.saveDecisions).not.toHaveBeenCalled()
    expect(api.buildArchive).toHaveBeenCalledOnce()
    expect(store.step).toBe(3)
  })

  it('accepts a blank questionnaire and exports the final honest draft without another question round', async () => {
    api.plan.mockImplementation(async (_id: string, decisions: AnalysisDecisions) => ({
      ...plan(decisions), automation: api.submitClarifications.mock.calls.length
        ? { phase: 'draft', stage: 'complete', humanRound: 1, questions: [] }
        : batchAutomation(),
    }))
    const store = useUploadWizardStore()
    await store.openProject(project('unknown'), { autoBuild: true })
    await store.answerQuestions({ changes: {}, notes: '' })
    expect(api.submitClarifications).toHaveBeenCalledOnce()
    expect(api.submitClarifications.mock.calls[0]![1].decisions.packages).toEqual({})
    expect(store.automation?.questions).toEqual([])
    expect(store.automation?.phase).toBe('draft')
    expect(store.step).toBe(3)
    expect(api.buildArchive).toHaveBeenCalledOnce()
    expect(api.saveDecisions).not.toHaveBeenCalled()
  })

  it('does not turn advanced edits into a submission or autosave before the batch submit', async () => {
    api.plan.mockImplementation(async (_id: string, decisions: AnalysisDecisions) => ({
      ...plan(decisions), automation: batchAutomation(),
    }))
    const store = useUploadWizardStore()
    await store.openProject(project('advanced'), { autoBuild: true })
    store.setPackage('first', 'requests')
    store.update({ declarationActions: { declared: { action: 'replace', requirement: 'numpy>=1.26' } } })
    await vi.advanceTimersByTimeAsync(5000)
    await store.refreshPlan()
    await store.build()
    await store.resolve({ pythonVersion: '3.11', platform: 'linux-x86_64' })
    expect(api.saveDecisions).not.toHaveBeenCalled()
    expect(api.submitClarifications).not.toHaveBeenCalled()
    expect(api.resolve).not.toHaveBeenCalled()
    expect(api.buildArchive).not.toHaveBeenCalled()
    expect(store.decisions?.packages).toEqual({ first: 'requests' })
    expect(store.canBuild).toBe(false)
  })

  it('can clear a package or version override while collecting the questionnaire', async () => {
    api.plan.mockImplementation(async (_id: string, decisions: AnalysisDecisions) => ({
      ...plan(decisions), automation: batchAutomation(),
    }))
    const store = useUploadWizardStore()
    await store.openProject(project('clear-overrides'))
    store.setPackage('first', 'requests')
    store.setVersion('first', '>=2')
    store.setPackage('first', '')
    store.setVersion('first', '')
    expect(store.decisions?.packages).toEqual({})
    expect(store.decisions?.versions).toEqual({})
    await vi.advanceTimersByTimeAsync(500)
    expect(api.saveDecisions).not.toHaveBeenCalled()
  })

  it('discards a submitted batch response after the user changes sessions', async () => {
    api.plan.mockImplementation(async (_id: string, decisions: AnalysisDecisions) => ({
      ...plan(decisions), automation: batchAutomation(),
    }))
    const store = useUploadWizardStore()
    await store.openProject(project('private'), { autoBuild: true })
    const saved = deferred<{ revision: number; decisions: AnalysisDecisions; updatedAt: null }>()
    api.submitClarifications.mockReturnValueOnce(saved.promise)
    const answering = store.answerQuestions({ changes: { packages: { first: 'requests' } }, notes: '' })
    const snapshot = api.submitClarifications.mock.calls[0]![1].decisions as AnalysisDecisions
    resetIntegrationSession()
    saved.resolve({ revision: 3, decisions: snapshot, updatedAt: null })
    await answering
    expect(store.project).toBeNull()
    expect(store.decisions).toBeNull()
    expect(api.plan).toHaveBeenCalledTimes(1)
    expect(api.buildArchive).not.toHaveBeenCalled()
  })

  it('keeps the whole current questionnaire after a failed atomic submission', async () => {
    api.plan.mockImplementation(async (_id: string, decisions: AnalysisDecisions) => ({
      ...plan(decisions), automation: batchAutomation(),
    }))
    const store = useUploadWizardStore()
    await store.openProject(project('save-error'), { autoBuild: true })
    api.submitClarifications.mockRejectedValueOnce(new ApiError(503, 'Unavailable'))
    await store.answerQuestions({ changes: { packages: { first: 'requests' } }, notes: '' })
    expect(store.automation?.questionnaireId).toBe('questionnaire-batch')
    expect(store.automation?.questions).toHaveLength(2)
    expect(store.decisions?.packages).toEqual({})
    expect(store.saveState).toBe('error')
    expect(store.isBusy).toBe(false)
    expect(api.saveDecisions).not.toHaveBeenCalled()
    expect(api.buildArchive).not.toHaveBeenCalled()
    api.plan.mockImplementation(async (_id: string, decisions: AnalysisDecisions) => ({
      ...plan(decisions), automation: { phase: 'draft', stage: 'complete', humanRound: 1, questions: [] },
    }))
    await store.answerQuestions({ changes: { packages: { first: 'requests' } }, notes: '' })
    expect(api.submitClarifications).toHaveBeenCalledTimes(2)
    expect(api.buildArchive).toHaveBeenCalledOnce()
    expect(api.saveDecisions).not.toHaveBeenCalled()
  })

  it('retries a plan read after a committed batch without submitting or saving the answers again', async () => {
    api.plan.mockImplementation(async (_id: string, decisions: AnalysisDecisions) => ({
      ...plan(decisions), automation: { phase: 'draft', stage: 'complete', humanRound: 1, questions: [] },
    }))
    api.plan.mockImplementationOnce(async (_id: string, decisions: AnalysisDecisions) => ({
      ...plan(decisions), automation: batchAutomation(),
    }))
    api.plan.mockRejectedValueOnce(new ApiError(503, 'Plan temporarily unavailable'))
    const store = useUploadWizardStore()
    await store.openProject(project('retry-plan'), { autoBuild: true })
    await store.answerQuestions({ changes: {}, notes: 'Unknown' })
    expect(store.saveState).toBe('saved')
    expect(store.isAutomating).toBe(true)
    await vi.advanceTimersByTimeAsync(2000)
    expect(api.submitClarifications).toHaveBeenCalledOnce()
    expect(api.saveDecisions).not.toHaveBeenCalled()
    expect(api.buildArchive).toHaveBeenCalledOnce()
    expect(store.step).toBe(3)
  })

  it('does not let a viewer submit the questionnaire', async () => {
    team.canEditProjects = false
    api.plan.mockImplementation(async (_id: string, decisions: AnalysisDecisions) => ({
      ...plan(decisions), automation: batchAutomation(),
    }))
    const store = useUploadWizardStore()
    await store.openProject(project('read-only'))
    await store.answerQuestions({ changes: {}, notes: 'not allowed' })
    expect(api.submitClarifications).not.toHaveBeenCalled()
    expect(api.saveDecisions).not.toHaveBeenCalled()
    expect(store.canBuild).toBe(false)
  })

  it('keeps a resolver outage usable as an automatically exported draft', async () => {
    api.plan.mockImplementation(async (_id: string, decisions: AnalysisDecisions) => ({
      ...plan(decisions),
      automaticResolution: { status: 'unavailable', reason: 'resolver_timeout' },
    }))
    const store = useUploadWizardStore()
    await store.openProject(project('offline'), { autoBuild: true })
    expect(store.step).toBe(3)
    expect(store.plan?.automaticResolution?.status).toBe('unavailable')
    expect(api.resolve).not.toHaveBeenCalled()
  })

  it('does not build a new upload whose plan is blocked', async () => {
    api.plan.mockImplementation(async (_id: string, decisions: AnalysisDecisions) => ({ ...plan(decisions), state: 'blocked' }))
    const store = useUploadWizardStore()
    await store.openProject(project('blocked'), { autoBuild: true })
    expect(store.canBuild).toBe(false)
    expect(store.step).toBe(2)
    expect(api.buildArchive).not.toHaveBeenCalled()
  })

  it('does not allow a late automatic resolution to build after switching projects', async () => {
    api.plan.mockImplementation(async (_id: string, decisions: AnalysisDecisions) => ({ ...plan(decisions), automaticResolution: { status: 'running' } }))
    const store = useUploadWizardStore()
    await store.openProject(project('old'), { autoBuild: true })
    const oldResult = deferred<PackagePlan>()
    api.plan.mockReturnValueOnce(oldResult.promise)
    await vi.advanceTimersByTimeAsync(2000)
    api.plan.mockImplementation(async (_id: string, decisions: AnalysisDecisions) => plan(decisions))
    await store.openProject(project('new'))
    oldResult.resolve({ ...plan(store.decisions!), automaticResolution: { status: 'ready' } })
    await Promise.resolve()
    await Promise.resolve()
    expect(store.project?.id).toBe('new')
    expect(api.buildArchive).not.toHaveBeenCalled()
  })

  it('keeps automatic prose out of author decisions until the user changes it', async () => {
    api.plan.mockImplementation(async (_id: string, decisions: AnalysisDecisions) => ({
      ...plan(decisions),
      description: decisions.description || 'Summarizes temperature measurements from a CSV file.',
      descriptionOrigin: decisions.description ? 'author' : 'automatic',
    }))
    const store = useUploadWizardStore()
    await store.openProject(project('example'))
    expect(store.plan?.descriptionOrigin).toBe('automatic')
    expect(store.decisions?.description).toBe('')
    await store.build()
    expect(api.saveDecisions.mock.calls[0]![1].description).toBe('')
    store.backToAnalysis()
    store.update({ description: 'Compares two calibrated thermometers.' })
    await store.build()
    expect(store.plan?.descriptionOrigin).toBe('author')
    expect(store.plan?.description).toBe('Compares two calibrated thermometers.')
  })

  it('retries an unsuccessful clarification save and recalculates before export', async () => {
    const store = useUploadWizardStore()
    await store.openProject(project('example'))
    store.update({ inputDescription: 'Request measurements.csv from the experiment author.' })
    api.saveDecisions.mockRejectedValueOnce(new ApiError(503, 'Unavailable'))
    await vi.advanceTimersByTimeAsync(500)
    expect(store.saveState).toBe('error')
    expect(store.plan).toBeNull()
    await store.refreshPlan()
    expect(store.saveState).toBe('saved')
    expect(store.plan?.decisions?.inputDescription).toBe(
      'Request measurements.csv from the experiment author.',
    )
    expect(store.isRefreshing).toBe(false)
    expect(api.buildArchive).not.toHaveBeenCalled()
    await store.build()
    expect(api.buildArchive).toHaveBeenCalledOnce()
  })

  it('explains an unavailable Docker template without submitting an invalid export', async () => {
    const store = useUploadWizardStore()
    await store.openProject(project('example'))
    store.includeDockerfile = true
    await store.build()
    expect(api.buildArchive).not.toHaveBeenCalled()
    expect(store.error).toBe('workflow.dockerUnavailable')
    expect(store.step).toBe(2)
    store.includeDockerfile = false
    await store.build()
    expect(api.buildArchive).toHaveBeenCalledOnce()
  })
  it('retains automatic launch provenance while saving data clarifications', async () => {
    api.plan.mockImplementation(async (_id: string, decisions: AnalysisDecisions) => ({ ...plan(decisions), runSpecOrigin: 'automatic' }))
    const store = useUploadWizardStore()
    await store.openProject(project('example'))
    store.update({ inputDescription: 'Ask the author for input.csv.' })
    await store.build()
    expect(api.saveDecisions.mock.calls[0]![1].runSpec).toBeNull()
    expect(store.plan?.runSpecOrigin).toBe('automatic')
  })

  it('lets viewers read a plan without sending edits or a build', async () => {
    team.canEditProjects = false
    const store = useUploadWizardStore()
    await store.openProject(project('example'), { autoBuild: true })
    expect(store.plan).not.toBeNull()
    expect(store.canBuild).toBe(false)
    store.update({ description: 'not allowed' })
    await store.build()
    await store.refreshPlan()
    expect(store.decisions?.description).toBe('')
    expect(api.saveDecisions).not.toHaveBeenCalled()
    expect(api.buildArchive).not.toHaveBeenCalled()
  })

  it('clears project data and rejects pending responses when the account changes', async () => {
    const result = deferred<ProjectAnalysis>()
    api.getAnalysis.mockReturnValueOnce(result.promise)
    const store = useUploadWizardStore()
    const opening = store.openProject(project('private'))
    resetIntegrationSession()
    result.resolve(analysis('private'))
    await opening
    expect(store.project).toBeNull()
    expect(store.analysis).toBeNull()
    expect(api.plan).not.toHaveBeenCalled()
  })

})
