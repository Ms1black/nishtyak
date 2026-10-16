import { computed, readonly, shallowRef } from 'vue'
import { ApiError, apiClient } from './client'

export interface IntegrationSession {
  mode: 'local' | 'preview' | 'proxy'
  user: { id: string; name: string; email: string }
  team: { id: string; name: string }
}

const session = shallowRef<IntegrationSession | null>(null)
let loading: Promise<IntegrationSession | null> | null = null

export const integrationSession = readonly(session)
export const isLocalWorkspace = computed(() => session.value?.mode === 'local')
export const isPreviewWorkspace = computed(() => session.value?.mode === 'preview')
export const isStandaloneWorkspace = computed(() => ['local', 'preview'].includes(session.value?.mode ?? ''))

export function getIntegrationSession(): Promise<IntegrationSession | null> {
  loading ??= apiClient.get<IntegrationSession>('/session').then((result) => {
    if (!['local', 'preview', 'proxy'].includes(result.mode) || !result.team?.id || !result.user?.id) {
      throw new ApiError(502, 'Invalid workspace session', 'INVALID_SESSION')
    }
    session.value = result
    return result
  }).catch((error: unknown) => {
    if (error instanceof ApiError && (error.status === 404 || error.status === 401)) return null
    loading = null
    throw error
  })
  return loading
}
