import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { ApiError, getIntegrationSession } from '@/shared/api'
import { userApi, type LoginCredentials } from '../api'
import { getInitials } from '../lib/initials'
import type { User } from './types'

export type SessionStatus = 'unknown' | 'authenticated' | 'anonymous' | 'local' | 'preview'

export const useUserStore = defineStore('user', () => {
  const currentUser = ref<User>({ id: '', name: '', email: '', avatarUrl: null })
  const status = ref<SessionStatus>('unknown')

  const isAuthenticated = computed(() => status.value === 'authenticated')
  const initials = computed(() => getInitials(currentUser.value.name))

  let checking: Promise<void> | null = null

  function ensureSession(): Promise<void> {
    if (status.value !== 'unknown') return Promise.resolve()
    checking ??= (async () => {
      try {
        const session = await getIntegrationSession()
        if (session && (session.mode === 'local' || session.mode === 'preview')) {
          currentUser.value = { ...session.user, avatarUrl: null }
          status.value = session.mode
          return
        }
        currentUser.value = await userApi.me()
        status.value = 'authenticated'
      } catch (e) {
        status.value = 'anonymous'
        if (!(e instanceof ApiError)) console.warn('Session check failed', e)
      } finally {
        checking = null
      }
    })()
    return checking
  }

  async function login(credentials: LoginCredentials) {
    currentUser.value = await userApi.login(credentials)
    status.value = 'authenticated'
  }

  async function logout() {
    try {
      await userApi.logout()
    } finally {
      status.value = 'anonymous'
    }
  }

  function markAnonymous() {
    status.value = 'anonymous'
  }

  async function updateName(name: string) {
    currentUser.value = await userApi.update({ name })
  }

  return { currentUser, status, isAuthenticated, initials, ensureSession, login, logout, markAnonymous, updateName }
})
