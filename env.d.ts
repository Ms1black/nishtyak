/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL?: string
  readonly VITE_USE_MOCK_API?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

import 'vue-router'
declare module 'vue-router' {
  interface RouteMeta {
    title?: string
    footerNote?: string
    public?: boolean
    guestOnly?: boolean
    layout?: 'main' | 'blank'
  }
}
