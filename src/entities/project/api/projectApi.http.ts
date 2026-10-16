import { apiClient } from '@/shared/api'
import type { ProjectApi } from './types'

export const projectHttpApi: ProjectApi = {
  list: (teamId) => apiClient.get(`/teams/${teamId}/projects`),

  upload: (teamId, file) => {
    const body = new FormData()
    body.append('file', file)
    return apiClient.post(`/teams/${teamId}/projects`, body)
  },

  getAnalysis: (projectId) => apiClient.get(`/projects/${projectId}/analysis`),

  getFileContent: (projectId, path) =>
    apiClient.get(`/projects/${projectId}/files?path=${encodeURIComponent(path)}`),

  getDecisions: (projectId) => apiClient.get(`/projects/${projectId}/decisions`),

  saveDecisions: (projectId, decisions, expectedRevision) =>
    apiClient.put(`/projects/${projectId}/decisions`, { expectedRevision, decisions }),

  plan: (projectId, decisions) => apiClient.post(`/projects/${projectId}/plan`, decisions),

  buildArchive: (projectId) => apiClient.post(`/projects/${projectId}/archive`),

  downloadArchive: (projectId) => apiClient.get(`/projects/${projectId}/archive`),
}
