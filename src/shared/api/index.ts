export { apiClient, ApiError, setUnauthorizedHandler } from './client'

export const USE_MOCK_API = import.meta.env.VITE_USE_MOCK_API !== 'false'
export { MOCK_USER_PHOTOS, MOCK_TEAM_PHOTOS } from './mockAvatars'
