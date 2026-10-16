import { ApiError, apiClient, MOCK_USER_PHOTOS, USE_MOCK_API } from '@/shared/api'
import { createUserAvatar, delay } from '@/shared/lib'
import type { User } from '../model/types'

export interface LoginCredentials {
  email: string
  password: string
}

export interface UserApi {
  me(): Promise<User>
  login(credentials: LoginCredentials): Promise<User>
  logout(): Promise<void>
  update(changes: Pick<User, 'name'>): Promise<User>
}

const MOCK_SESSION_KEY = 'nishtyak:mock-session'

let me: User = {
  id: 'u-1',
  name: 'Алексей Ковалёв',
  email: 'a.kovalev@nishtyak.dev',
  avatarUrl: MOCK_USER_PHOTOS['u-1'] ?? createUserAvatar('u-1'),
}

function hasMockSession(): boolean {
  try {
    return localStorage.getItem(MOCK_SESSION_KEY) === '1'
  } catch {
    return false
  }
}

function setMockSession(active: boolean) {
  try {
    if (active) localStorage.setItem(MOCK_SESSION_KEY, '1')
    else localStorage.removeItem(MOCK_SESSION_KEY)
  } catch {
  }
}

const mockApi: UserApi = {
  async me() {
    await delay(150)
    if (!hasMockSession()) throw new ApiError(401, 'UNAUTHORIZED')
    return { ...me }
  },
  async login({ email, password }) {
    await delay(600)
    if (password.length < 6) throw new ApiError(401, 'INVALID_CREDENTIALS')
    me = { ...me, email }
    setMockSession(true)
    return { ...me }
  },
  async logout() {
    await delay(200)
    setMockSession(false)
  },
  async update(changes) {
    await delay(400)
    me = { ...me, ...changes }
    return { ...me }
  },
}

const httpApi: UserApi = {
  me: () => apiClient.get('/me'),
  login: (credentials) => apiClient.post('/auth/login', credentials),
  logout: () => apiClient.post('/auth/logout'),
  update: (changes) => apiClient.patch('/me', changes),
}

export const userApi: UserApi = USE_MOCK_API ? mockApi : httpApi
