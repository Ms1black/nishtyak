import { apiClient } from '@/shared/api'
import type { User } from '../model/types'

export interface LoginCredentials {
  email: string
  password: string
}

export interface RegistrationCredentials extends LoginCredentials {
  name: string
}

export const userApi = {
  me: () => apiClient.get<User>('/me'),
  login: (credentials: LoginCredentials) => apiClient.post<User>('/auth/login', credentials),
  register: (credentials: RegistrationCredentials) => apiClient.post<User>('/auth/register', credentials),
  logout: () => apiClient.post<void>('/auth/logout'),
  update: (changes: Pick<User, 'name'>) => apiClient.patch<User>('/me', changes),
}
