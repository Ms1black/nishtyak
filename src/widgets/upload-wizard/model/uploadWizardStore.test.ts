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
    api.buildArchive.mockImplementation(async (id: string) => ({
      ...project(id),
      archive: { status: 'ready', sizeBytes: 500 },
    }))
  })

  afterEach(() => {
    vi.useRealTimers()
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
    await store.openProject(project('example'))
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
