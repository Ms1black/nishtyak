import { computed, readonly, shallowRef } from 'vue'
import { ApiError, apiClient } from './client'

export interface IntegrationSession {
  mode: 'local' | 'preview' | 'proxy' | 'accounts'
  user: { id: string; name: string; email: string; avatarUrl?: string | null }
  team: { id: string; name: string }
}

const session = shallowRef<IntegrationSession | null>(null)
const generation = shallowRef(0)
let loading: Promise<IntegrationSession | null> | null = null

export const sessionGeneration = readonly(generation)
export const integrationSession = readonly(session)
export const isLocalWorkspace = computed(() => session.value?.mode === 'local')
export const isPreviewWorkspace = computed(() => session.value?.mode === 'preview')
export const isStandaloneWorkspace = computed(() => ['local', 'preview'].includes(session.value?.mode ?? ''))

export function resetIntegrationSession() {
  session.value = null
  loading = null
  generation.value += 1
}

export function getIntegrationSession(): Promise<IntegrationSession | null> {
  const current = generation.value
  loading ??= apiClient.get<IntegrationSession>('/session').then((result) => {
    if (current !== generation.value) return null
    if (!['local', 'preview', 'proxy', 'accounts'].includes(result.mode) || !result.team?.id || !result.user?.id) {
      throw new ApiError(502, 'Invalid workspace session', 'INVALID_SESSION')
    }
    session.value = result
    return result
  }).catch((error: unknown) => {
    if (current !== generation.value) return null
    loading = null
    if (error instanceof ApiError && (error.status === 404 || error.status === 401)) return null
    throw error
  })
  return loading
}
