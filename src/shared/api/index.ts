export { apiClient, ApiError, setUnauthorizedHandler } from './client'

export const USE_MOCK_API = import.meta.env.VITE_USE_MOCK_API === 'true'
export { MOCK_USER_PHOTOS, MOCK_TEAM_PHOTOS } from './mockAvatars'
export { getIntegrationSession, resetIntegrationSession, sessionGeneration, integrationSession, isLocalWorkspace, isPreviewWorkspace, isStandaloneWorkspace } from './session'
