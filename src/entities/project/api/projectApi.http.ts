import { apiClient } from '@/shared/api'
import type { ProjectApi } from './types'

export const projectHttpApi: ProjectApi = {
  capabilities: () => apiClient.get('/capabilities'),

  list: (teamId) => apiClient.get(`/teams/${teamId}/projects`),

  get: (projectId) => apiClient.get(`/projects/${projectId}`),

  upload: (teamId, file) => {
    const body = new FormData()
    body.append('file', file)
    return apiClient.post(`/teams/${teamId}/projects`, body)
  },

  getAnalysis: (projectId) => apiClient.get(`/projects/${projectId}/analysis`),

  getFileContent: (projectId, path) =>
    apiClient.get(`/projects/${projectId}/files?path=${encodeURIComponent(path)}`),

  getArchiveFileContent: (projectId, path, archiveTag) =>
    apiClient.get(`/projects/${projectId}/archive/files?path=${encodeURIComponent(path)}&archiveTag=${encodeURIComponent(archiveTag)}`),

  getDecisions: (projectId) => apiClient.get(`/projects/${projectId}/decisions`),

  saveDecisions: (projectId, decisions, expectedRevision) =>
    apiClient.put(`/projects/${projectId}/decisions`, { expectedRevision, decisions }),

  submitClarifications: (projectId, answers) => apiClient.post(`/projects/${projectId}/clarifications`, answers),

  plan: (projectId, decisions) => apiClient.post(`/projects/${projectId}/plan`, decisions),

  buildArchive: (projectId, options) => apiClient.post(`/projects/${projectId}/archive`, options),

  downloadArchive: (projectId) => apiClient.get(`/projects/${projectId}/archive`),

  getReadme: (projectId) => apiClient.get(`/projects/${projectId}/readme`),

  getVerification: (projectId) => apiClient.get(`/projects/${projectId}/verification`),

  saveVerification: (projectId, verification) => apiClient.put(`/projects/${projectId}/verification`, verification),

  replaceSource: (projectId, file, expectedRevision) => {
    const body = new FormData()
    body.append('file', file)
    body.append('expectedRevision', String(expectedRevision))
    return apiClient.post(`/projects/${projectId}/source`, body)
  },

  reanalyze: (projectId, options, authorEnvironment) =>
    apiClient.post(`/projects/${projectId}/analysis`, {
      options,
      ...(authorEnvironment === undefined ? {} : { authorEnvironment }),
    }),

  resolve: (projectId, target, decisions) =>
    apiClient.post(`/projects/${projectId}/resolve`, { target, decisions }),

  getLock: (projectId) => apiClient.get(`/projects/${projectId}/lock`),

  getGraph: (projectId) => apiClient.get(`/projects/${projectId}/graph`),

  remove: (projectId) => apiClient.delete(`/projects/${projectId}`),
}
