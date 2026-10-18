// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { useTeamStore } from '@/entities/team'
import { RouteName } from '@/shared/config'

const { api, projects } = vi.hoisted(() => ({
  api: { listMembers: vi.fn(), listInvites: vi.fn() },
  projects: { list: vi.fn() },
}))
vi.mock('@/entities/team/api', () => ({ teamApi: api }))
vi.mock('@/entities/project/api', () => ({ projectApi: projects }))
vi.mock('@/shared/api', async (original) => ({
  ...await original<typeof import('@/shared/api')>(),
  isStandaloneWorkspace: (await import('vue')).ref(false),
}))

let application: App | undefined
let container: HTMLElement | undefined

afterEach(() => {
  application?.unmount()
  container?.remove()
  vi.resetAllMocks()
})

describe('team page navigation', () => {
  it.each(['owner', 'member', 'viewer'] as const)(
    'opens members and invitations from project tabs for a %s without blocking navigation',
    async (role) => {
      const pinia = createPinia()
      setActivePinia(pinia)
      const teams = useTeamStore()
      teams.teams = [{ id: 'team', name: 'Laboratory', myRole: role, membersCount: 1, avatarUrl: null }]
      teams.switchTeam('team')
      projects.list.mockResolvedValue([])
      api.listMembers.mockResolvedValue([{
        id: 'author', name: 'Author', email: 'author@example.test', role: 'owner',
        joinedAt: '2026-10-17T12:00:00Z', isYou: role === 'owner', avatarUrl: null,
      }])
      api.listInvites.mockResolvedValue([])
      const router = createRouter({
        history: createMemoryHistory(),
        routes: [
          { path: '/team/projects', name: RouteName.TeamProjects, component: () => import('@/pages/team-projects') },
          { path: '/team/members', name: RouteName.TeamMembers, component: () => import('@/pages/team-members') },
          { path: '/team/invite', name: RouteName.TeamInvite, component: () => import('@/pages/team-invite') },
          { path: '/upload', name: RouteName.Upload, component: {} },
        ],
      })
      await router.push('/team/projects')
      await router.isReady()
      container = document.createElement('div')
      document.body.append(container)
      application = createApp({ render: () => h(RouterView) }).use(pinia).use(router)
      application.config.globalProperties.$t = (key: string) => key
      application.mount(container)
      await nextTick()

      const members = container.querySelector<HTMLAnchorElement>('a[href="/team/members"]')!
      expect(members.getAttribute('role')).toBe('tab')
      members.click()
      await vi.waitFor(() => expect(router.currentRoute.value.name).toBe(RouteName.TeamMembers))
      await vi.waitFor(() => expect(api.listMembers).toHaveBeenCalledWith('team'))
      expect(container.textContent).toContain('Author')

      container.querySelector<HTMLAnchorElement>('a[href="/team/invite"]')!.click()
      await vi.waitFor(() => expect(router.currentRoute.value.name).toBe(RouteName.TeamInvite))
      if (role === 'owner') {
        await vi.waitFor(() => expect(api.listInvites).toHaveBeenCalledWith('team'))
        expect(container.textContent).toContain('invite.title')
      } else {
        expect(container.textContent).toContain('invite.managerOnly')
        expect(api.listInvites).not.toHaveBeenCalled()
      }
    },
  )
})
