// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, type App } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { TeamTabs } from '@/widgets/team-tabs'
import { RouteName } from '@/shared/config'
import { installChunkRecovery } from './chunkRecovery'

let application: App | undefined
let container: HTMLElement | undefined

afterEach(() => {
  application?.unmount()
  container?.remove()
  sessionStorage.clear()
  localStorage.clear()
})

describe('route chunk recovery', () => {
  it.each([
    'Failed to fetch dynamically imported module: https://workspace.test/assets/team-members-old.js',
    'error loading dynamically imported module: https://workspace.test/assets/team-members-old.js',
    'Importing a module script failed.',
    'Unable to preload CSS for /assets/team-members-old.css',
  ])('recovers a failed team tab click once per build and route: %s', async (message) => {
    const failure = new TypeError(message)
    const reload = vi.fn()
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/team/projects', name: RouteName.TeamProjects, component: TeamTabs },
        { path: '/team/members', name: RouteName.TeamMembers, component: () => Promise.reject(failure) },
        { path: '/team/invite', name: RouteName.TeamInvite, component: {} },
      ],
    })
    const browser = { sessionStorage, location: { origin: 'https://workspace.test', assign: reload } }
    const remove = installChunkRecovery(router, 'index-build-one.js', browser)
    localStorage.setItem('nishtyak:current-team', 'selected-team')
    await router.push('/team/projects')
    container = document.createElement('div')
    document.body.append(container)
    application = createApp({ render: () => h(RouterView) }).use(router)
    application.config.globalProperties.$t = (key: string) => key
    application.mount(container)

    container.querySelector<HTMLAnchorElement>('a[href="/team/members"]')!.click()
    await vi.waitFor(() => expect(reload).toHaveBeenCalledExactlyOnceWith('https://workspace.test/team/members'))
    expect(router.currentRoute.value.name).toBe(RouteName.TeamProjects)
    expect(localStorage.getItem('nishtyak:current-team')).toBe('selected-team')

    // The marker survives the document reload; another failed import cannot loop.
    remove()
    const removeSecond = installChunkRecovery(router, 'index-build-one.js', browser)
    await expect(router.push('/team/members')).rejects.toBe(failure)
    expect(reload).toHaveBeenCalledTimes(1)
    removeSecond()
    installChunkRecovery(router, 'index-build-two.js', browser)
    await expect(router.push('/team/members')).rejects.toBe(failure)
    expect(reload).toHaveBeenCalledTimes(2)
  })

  it('preserves the intended query and hash without retrying unrelated application failures', async () => {
    const reload = vi.fn()
    const failure = new Error('Failed to fetch dynamically imported module')
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/team/projects', component: {} },
        { path: '/team/invite', component: () => Promise.reject(failure) },
        { path: '/broken', component: () => Promise.reject(new Error('Invalid team response')) },
      ],
    })
    installChunkRecovery(router, 'build', { sessionStorage, location: { origin: 'https://workspace.test', assign: reload } })
    await router.push('/team/projects')
    await expect(router.push('/broken')).rejects.toThrow('Invalid team response')
    expect(reload).not.toHaveBeenCalled()
    await expect(router.push('/team/invite?role=member#form')).rejects.toBe(failure)
    expect(reload).toHaveBeenCalledExactlyOnceWith('https://workspace.test/team/invite?role=member#form')
  })

  it('does not reload when the retry marker cannot be saved', async () => {
    const reload = vi.fn()
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: {} },
        { path: '/team/members', component: () => Promise.reject(new Error('Importing a module script failed.')) },
      ],
    })
    const deniedStorage = { getItem: () => null, setItem: () => { throw new Error('Storage denied') } } as unknown as Storage
    installChunkRecovery(router, 'build', { sessionStorage: deniedStorage, location: { origin: 'https://workspace.test', assign: reload } })
    await router.push('/')
    await expect(router.push('/team/members')).rejects.toThrow('Importing a module script failed.')
    expect(reload).not.toHaveBeenCalled()
  })

  it('rejects a target outside this application origin', async () => {
    const reload = vi.fn()
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: {} },
        { path: '/team/members', component: () => Promise.reject(new Error('Importing a module script failed.')) },
      ],
    })
    installChunkRecovery(router, 'build', { sessionStorage, location: { origin: 'https://workspace.test', assign: reload } })
    await router.push('/')
    vi.spyOn(router.options.history, 'createHref').mockReturnValue('//outside.test/team/members')
    await expect(router.push('/team/members')).rejects.toThrow('Importing a module script failed.')
    expect(reload).not.toHaveBeenCalled()
    expect(sessionStorage.length).toBe(0)
  })
})
