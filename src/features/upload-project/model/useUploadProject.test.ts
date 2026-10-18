import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { effectScope, nextTick, reactive, type EffectScope } from 'vue'
import { ApiError, resetIntegrationSession } from '@/shared/api'
import type { Project } from '@/entities/project/model/types'
import { useUploadProject } from './useUploadProject'

const { dependencies, upload, upsert } = vi.hoisted(() => ({
  dependencies: {} as Record<string, unknown>, upload: vi.fn(), upsert: vi.fn(),
}))
vi.mock('@/entities/team', () => ({ useTeamStore: () => dependencies.team }))
vi.mock('@/entities/project', () => ({ projectApi: { upload }, useProjectStore: () => ({ upsert }) }))

let scope: EffectScope
let team: { currentTeamId: string | null; currentTeam: { id: string } | null; canEditProjects: boolean; init: ReturnType<typeof vi.fn> }

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (cause: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

const zip = (name = 'project.zip') => new File(['archive'], name)
const settle = async () => { await Promise.resolve(); await nextTick() }
const create = () => scope.run(useUploadProject)!

describe('workspace-aware project upload', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    scope = effectScope()
    team = reactive({ currentTeamId: 'team', currentTeam: { id: 'team' }, canEditProjects: true, init: vi.fn(async () => {}) })
    dependencies.team = team
  })
  afterEach(() => scope.stop())

  it('rejects an entire multi-file selection instead of uploading its first file', async () => {
    const intake = create()
    await settle()
    intake.select([zip()])
    expect(intake.canUpload.value).toBe(true)
    intake.select([zip('one.zip'), zip('two.zip')])
    expect(intake.file.value).toBeNull()
    expect(intake.error.value).toBe('upload.errors.multiple')
    expect(intake.canUpload.value).toBe(false)
    expect(upload).not.toHaveBeenCalled()
    intake.select([zip()])
    expect(intake.error.value).toBeNull()
    intake.select(null)
    expect(intake.file.value).toBeNull()
  })

  it('keeps the selected archive while workspace initialization fails and allows retry', async () => {
    const pending = deferred<void>()
    team.currentTeam = null
    team.currentTeamId = null
    team.init.mockReturnValueOnce(pending.promise)
    const intake = create()
    intake.select([zip()])
    expect(intake.workspaceStatus.value).toBe('loading')
    expect(intake.canUpload.value).toBe(false)
    await intake.upload()
    expect(intake.error.value).toBe('upload.workspace.loading')
    pending.reject(new ApiError(503, 'Unavailable'))
    await settle()
    expect(intake.workspaceStatus.value).toBe('error')
    expect(intake.workspaceError.value).toBe('upload.workspace.error')
    expect(intake.file.value?.name).toBe('project.zip')
    team.init.mockImplementationOnce(async () => { team.currentTeamId = 'team'; team.currentTeam = { id: 'team' } })
    await intake.loadWorkspace()
    expect(intake.workspaceStatus.value).toBe('ready')
    expect(intake.canUpload.value).toBe(true)
    expect(intake.error.value).toBeNull()
    const project = { id: 'uploaded' } as Project
    upload.mockResolvedValueOnce(project)
    expect(await intake.upload()).toEqual(project)
    expect(upload).toHaveBeenCalledExactlyOnceWith('team', expect.any(File))
    expect(upsert).toHaveBeenCalledWith(project)
  })

  it('reports missing workspace and viewer restrictions instead of a silent no-op', async () => {
    team.currentTeam = null
    const intake = create()
    intake.select([zip()])
    await settle()
    expect(intake.workspaceStatus.value).toBe('error')
    await intake.upload()
    expect(intake.error.value).toBe('upload.workspace.error')
    team.currentTeam = { id: 'team' }
    team.canEditProjects = false
    await intake.loadWorkspace()
    await intake.upload()
    expect(intake.error.value).toBe('teams.readOnly')
    expect(intake.canUpload.value).toBe(false)
    expect(upload).not.toHaveBeenCalled()
  })

  it('does not retain a previous account selection or apply its pending upload', async () => {
    const intake = create()
    await settle()
    intake.select([zip()])
    const pending = deferred<Project>()
    upload.mockReturnValueOnce(pending.promise)
    const uploading = intake.upload()
    resetIntegrationSession()
    await settle()
    expect(intake.file.value).toBeNull()
    pending.resolve({ id: 'old-account' } as Project)
    expect(await uploading).toBeNull()
    expect(upsert).not.toHaveBeenCalled()
  })

  it('does not open an upload after its screen has been disposed', async () => {
    const intake = create()
    await settle()
    intake.select([zip()])
    const pending = deferred<Project>()
    upload.mockReturnValueOnce(pending.promise)
    const uploading = intake.upload()
    scope.stop()
    pending.resolve({ id: 'late-upload' } as Project)
    expect(await uploading).toBeNull()
    expect(upsert).not.toHaveBeenCalled()
  })
})
