import type { Router } from 'vue-router'

type RecoveryBrowser = Pick<Window, 'sessionStorage'> & {
  location: Pick<Location, 'origin' | 'assign'>
}

const chunkLoadError = /Failed to fetch dynamically imported module|error loading dynamically imported module|Importing a module script failed|Unable to preload CSS for /i

export function installChunkRecovery(router: Router, build: string, browser: RecoveryBrowser = window) {
  return router.onError((error, to) => {
    if (!(error instanceof Error) || !chunkLoadError.test(error.message)) return
    try {
      const target = new URL(router.options.history.createHref(to.fullPath), browser.location.origin)
      if (target.origin !== browser.location.origin) return

      // A removed deployment chunk needs fresh HTML, not another import of the cached failure.
      const key = `nishtyak.chunk-recovery:${build}:${target.pathname}`
      if (browser.sessionStorage.getItem(key)) return
      browser.sessionStorage.setItem(key, '1')
      browser.location.assign(target.href)
    } catch {
      // If session storage is unavailable, avoid an unbounded reload loop.
    }
  })
}
