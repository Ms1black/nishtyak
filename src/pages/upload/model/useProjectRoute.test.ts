import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { effectScope, nextTick, reactive, type EffectScope } from 'vue'
import type { Project } from '@/entities/project/model/types'
import { useProjectRoute } from './useProjectRoute'
import { ApiError } from '@/shared/api'

const { dependencies } = vi.hoisted(() => ({ dependencies: {} as Record<string, unknown> }))

vi.mock('vue-router', () => ({ useRoute: () => dependencies.route, useRouter: () => dependencies.router }))
vi.mock('@/entities/project', () => ({ useProjectStore: () => dependencies.projects, projectApi: { get: (...args: unknown[]) => (dependencies.get as (...values: unknown[]) => unknown)(...args) } }))
vi.mock('@/entities/team', () => ({ useTeamStore: () => dependencies.team }))
vi.mock('@/widgets/upload-wizard', () => ({ useUploadWizardStore: () => dependencies.wizard }))

let scope: EffectScope
let route: { query: Record<string, string> }
let wizard: { project: Project | null; openProject: ReturnType<typeof vi.fn>; reset: ReturnType<typeof vi.fn<() => void>> }
let projects: { upsert: ReturnType<typeof vi.fn> }
let get: ReturnType<typeof vi.fn>
let replace: ReturnType<typeof vi.fn>

const project = (id: string) => ({ id }) as Project

async function settle() {
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

describe('project URL and wizard coordination', () => {
  beforeEach(() => {
    scope = effectScope()
    route = reactive({ query: {} })
    wizard = reactive({ project: null, openProject: vi.fn(), reset: vi.fn() })
    wizard.openProject.mockImplementation(async (value: Project) => { wizard.project = value })
    wizard.reset.mockImplementation(() => { wizard.project = null })
    projects = { upsert: vi.fn() }
    get = vi.fn(async (id: string) => ({ project: project(id), teamId: 'workspace' }))
    const team = reactive({
      currentTeamId: 'workspace', teams: [{ id: 'workspace' }, { id: 'other' }],
      init: vi.fn(async () => {}), refresh: vi.fn(async () => {}),
      switchTeam: vi.fn((id: string) => { team.currentTeamId = id }),
    })
    replace = vi.fn(async ({ query }: { query: Record<string, string> }) => { route.query = query })
    Object.assign(dependencies, {
      route, wizard, projects, get, router: { replace }, team,
    })
  })

  afterEach(() => scope.stop())

  it('restores a bookmarked project without removing its URL or opening it twice', async () => {
    route.query = { project: 'saved' }
    scope.run(useProjectRoute)
    await settle()
    expect(wizard.openProject).toHaveBeenCalledExactlyOnceWith(project('saved'))
    expect(route.query.project).toBe('saved')
    expect(replace).not.toHaveBeenCalled()
  })

  it('keeps a newly uploaded project in the URL and removes it when the wizard resets', async () => {
    route.query = { source: 'example' }
    scope.run(useProjectRoute)
    wizard.project = project('uploaded')
    await settle()
    expect(route.query).toEqual({ source: 'example', project: 'uploaded' })
    expect(wizard.openProject).not.toHaveBeenCalled()
    wizard.reset()
    await settle()
    expect(route.query).toEqual({ source: 'example' })
  })

  it('does not let an older pending project lookup replace the selected project', async () => {
    let finish!: (value: { project: Project; teamId: string }) => void
    get.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    route.query = { project: 'old' }
    scope.run(useProjectRoute)
    await settle()
    route.query = { project: 'new' }
    await settle()
    finish({ project: project('old'), teamId: 'workspace' })
    await settle()
    expect(wizard.openProject).toHaveBeenCalledExactlyOnceWith(project('new'))
    expect(wizard.project?.id).toBe('new')
    expect(route.query.project).toBe('new')
  })

  it('resets the displayed project when navigating to a new upload without a project ID', async () => {
    wizard.project = project('saved')
    route.query = { project: 'saved' }
    scope.run(useProjectRoute)
    route.query = {}
    await settle()
    expect(wizard.project).toBeNull()
    expect(route.query.project).toBeUndefined()
  })

  it('opens a project from another authorized workspace and selects that workspace', async () => {
    route.query = { project: 'shared' }
    get.mockResolvedValueOnce({ project: project('shared'), teamId: 'other' })
    scope.run(useProjectRoute)
    await settle()
    expect((dependencies.team as { switchTeam: ReturnType<typeof vi.fn> }).switchTeam).toHaveBeenCalledWith('other')
    expect(wizard.project?.id).toBe('shared')
    expect(route.query.project).toBe('shared')
  })

  it.each([403, 404, 503])('preserves the project ID and offers a retry after HTTP %s', async (status) => {
    route.query = { project: 'saved' }
    get.mockRejectedValueOnce(new ApiError(status, 'Unavailable'))
    const state = scope.run(useProjectRoute)!
    await settle()
    expect(route.query.project).toBe('saved')
    expect(replace).not.toHaveBeenCalled()
    expect(state.openError.value).not.toBeNull()
    expect(state.isOpening.value).toBe(false)
    state.retry()
    await settle()
    expect(state.openError.value).toBeNull()
    expect(wizard.project?.id).toBe('saved')
    expect(route.query.project).toBe('saved')
  })

  it('does not switch to an inaccessible workspace returned by a stale session', async () => {
    route.query = { project: 'private' }
    get.mockResolvedValueOnce({ project: project('private'), teamId: 'unknown' })
    const state = scope.run(useProjectRoute)!
    await settle()
    expect(state.openError.value).toBe('upload.open.forbidden')
    expect(wizard.openProject).not.toHaveBeenCalled()
    expect(route.query.project).toBe('private')
  })

  it('does not undo a manual workspace selection while a project lookup is pending', async () => {
    let finish!: (value: { project: Project; teamId: string }) => void
    get.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    route.query = { project: 'slow' }
    const state = scope.run(useProjectRoute)!
    await settle()
    const team = dependencies.team as { currentTeamId: string; switchTeam: (id: string) => void }
    team.switchTeam('other')
    finish({ project: project('slow'), teamId: 'workspace' })
    await settle()
    expect(team.currentTeamId).toBe('other')
    expect(wizard.openProject).not.toHaveBeenCalled()
    expect(state.openError.value).toBe('upload.open.workspaceChanged')
    expect(route.query.project).toBe('slow')
  })

  it('does not undo a manual workspace selection while refreshing a previously unknown team', async () => {
    let finish!: () => void
    const team = dependencies.team as {
      currentTeamId: string; teams: { id: string }[]; switchTeam: (id: string) => void;
      refresh: ReturnType<typeof vi.fn<() => Promise<void>>>;
    }
    team.refresh.mockImplementationOnce(() => new Promise<void>((resolve) => { finish = resolve }))
    get.mockResolvedValueOnce({ project: project('new-team-project'), teamId: 'new-team' })
    route.query = { project: 'new-team-project' }
    const state = scope.run(useProjectRoute)!
    await settle()
    team.switchTeam('other')
    team.teams.push({ id: 'new-team' })
    finish()
    await settle()
    expect(team.currentTeamId).toBe('other')
    expect(wizard.openProject).not.toHaveBeenCalled()
    expect(state.openError.value).toBe('upload.open.workspaceChanged')
    expect(route.query.project).toBe('new-team-project')
  })
})
