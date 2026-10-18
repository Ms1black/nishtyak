import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { ApiError, getIntegrationSession, resetIntegrationSession, sessionGeneration } from '@/shared/api'
import { userApi, type LoginCredentials, type RegistrationCredentials } from '../api'
import { getInitials } from '../lib/initials'
import type { User } from './types'

export type SessionStatus = 'unknown' | 'authenticated' | 'anonymous' | 'local' | 'preview'

const emptyUser = (): User => ({ id: '', name: '', email: '', avatarUrl: null })

export const useUserStore = defineStore('user', () => {
  const currentUser = ref<User>(emptyUser())
  const status = ref<SessionStatus>('unknown')
  const isAuthenticated = computed(() => status.value === 'authenticated')
  const initials = computed(() => getInitials(currentUser.value.name))
  let checking: Promise<void> | null = null

  function ensureSession(): Promise<void> {
    if (status.value !== 'unknown') return Promise.resolve()
    const generation = sessionGeneration.value
    checking ??= (async () => {
      try {
        const session = await getIntegrationSession()
        if (generation !== sessionGeneration.value) return
        if (session && session.mode !== 'proxy') {
          currentUser.value = { avatarUrl: null, ...session.user }
          status.value = session.mode === 'local' || session.mode === 'preview' ? session.mode : 'authenticated'
          return
        }
        const user = await userApi.me()
        if (generation !== sessionGeneration.value) return
        currentUser.value = user
        status.value = 'authenticated'
      } catch (e) {
        if (generation !== sessionGeneration.value) return
        currentUser.value = emptyUser()
        status.value = 'anonymous'
        if (!(e instanceof ApiError)) console.warn('Session check failed', e)
      } finally {
        if (generation === sessionGeneration.value) checking = null
      }
    })()
    return checking
  }

  async function signedIn(user: User) {
    resetIntegrationSession()
    checking = null
    currentUser.value = user
    status.value = 'authenticated'
    await getIntegrationSession()
  }

  async function login(credentials: LoginCredentials) {
    const generation = sessionGeneration.value
    const user = await userApi.login(credentials)
    if (generation !== sessionGeneration.value) throw new ApiError(409, 'Session changed', 'SESSION_CHANGED')
    await signedIn(user)
  }

  async function register(credentials: RegistrationCredentials) {
    const generation = sessionGeneration.value
    const user = await userApi.register(credentials)
    if (generation !== sessionGeneration.value) throw new ApiError(409, 'Session changed', 'SESSION_CHANGED')
    await signedIn(user)
  }

  function markAnonymous() {
    resetIntegrationSession()
    checking = null
    currentUser.value = emptyUser()
    status.value = 'anonymous'
  }

  async function logout() {
    try {
      await userApi.logout()
    } finally {
      markAnonymous()
    }
  }

  async function updateName(name: string) {
    const generation = sessionGeneration.value
    const user = await userApi.update({ name })
    if (generation === sessionGeneration.value) currentUser.value = user
  }

  return { currentUser, status, isAuthenticated, initials, ensureSession, login, register, logout, markAnonymous, updateName }
})
