import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { effectScope, nextTick, reactive, type EffectScope } from 'vue'
import type { Project } from '@/entities/project/model/types'
import { useProjectRoute } from './useProjectRoute'

const { dependencies } = vi.hoisted(() => ({ dependencies: {} as Record<string, unknown> }))

vi.mock('vue-router', () => ({ useRoute: () => dependencies.route, useRouter: () => dependencies.router }))
vi.mock('@/entities/project', () => ({ useProjectStore: () => dependencies.projects }))
vi.mock('@/entities/team', () => ({ useTeamStore: () => dependencies.team }))
vi.mock('@/widgets/upload-wizard', () => ({ useUploadWizardStore: () => dependencies.wizard }))

let scope: EffectScope
let route: { query: Record<string, string> }
let wizard: { project: Project | null; openProject: ReturnType<typeof vi.fn>; reset: ReturnType<typeof vi.fn<() => void>> }
let projects: { getById: ReturnType<typeof vi.fn>; fetchAll: ReturnType<typeof vi.fn> }
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
    projects = { getById: vi.fn((id: string) => project(id)), fetchAll: vi.fn() }
    replace = vi.fn(async ({ query }: { query: Record<string, string> }) => { route.query = query })
    Object.assign(dependencies, {
      route, wizard, projects, router: { replace }, team: { currentTeamId: 'workspace', init: vi.fn(async () => {}) },
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
    let finish!: () => void
    projects.getById.mockImplementation((id: string) => id === 'old' ? undefined : project(id))
    projects.fetchAll.mockImplementation(() => new Promise<void>((resolve) => { finish = resolve }))
    route.query = { project: 'old' }
    scope.run(useProjectRoute)
    await settle()
    route.query = { project: 'new' }
    await settle()
    finish()
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
})
