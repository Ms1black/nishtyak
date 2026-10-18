import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

const { bootstrap } = vi.hoisted(() => ({
  bootstrap: { router: null as Router | null, initialPath: '', mount: vi.fn() },
}))

vi.mock('vue', async (original) => ({
  ...await original<typeof import('vue')>(),
  createApp: () => {
    const app = {
      use(plugin: unknown) {
        if (bootstrap.router && plugin === bootstrap.router) void bootstrap.router.push(bootstrap.initialPath)
        return app
      },
      mount: bootstrap.mount,
    }
    return app
  },
}))
vi.mock('./App.vue', () => ({ default: {} }))
vi.mock('./router', () => ({ get router() { return bootstrap.router } }))
vi.mock('@/shared/i18n', () => ({ i18n: {} }))

describe('cold public page bootstrap', () => {
  let finishSessionCheck: () => void

  beforeEach(() => {
    vi.resetModules()
    bootstrap.mount.mockReset()
    const sessionCheck = new Promise<void>((resolve) => { finishSessionCheck = resolve })
    bootstrap.router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/register', component: {}, meta: { public: true, layout: 'blank' } },
        { path: '/invite/team', component: {}, meta: { public: true, layout: 'blank' } },
        { path: '/login', component: {}, meta: { public: true, layout: 'blank' } },
      ],
    })
    bootstrap.router.beforeEach(async () => { await sessionCheck })
  })

  afterEach(() => { bootstrap.router = null })

  it.each(['/register', '/invite/team#token=' + 'a'.repeat(43)])(
    'waits for the session guard before rendering %s', async (path) => {
      bootstrap.initialPath = path
      let mountedPath: string | undefined
      bootstrap.mount.mockImplementation(() => {
        const route = bootstrap.router!.currentRoute.value
        mountedPath = route.fullPath
        // The application layout starts team requests. An unauthenticated response
        // redirects to login when the initial public route has not resolved yet.
        if (!route.meta.public) void bootstrap.router!.replace('/login')
      })

      await import('./main')
      expect(bootstrap.mount).not.toHaveBeenCalled()
      expect(bootstrap.router!.currentRoute.value.matched).toEqual([])

      finishSessionCheck()
      await bootstrap.router!.isReady()
      await vi.waitFor(() => expect(bootstrap.mount).toHaveBeenCalledExactlyOnceWith('#app'))
      expect(mountedPath).toBe(path)
      expect(bootstrap.router!.currentRoute.value.fullPath).toBe(path)
      expect(bootstrap.router!.currentRoute.value.meta.layout).toBe('blank')
    },
  )
})
