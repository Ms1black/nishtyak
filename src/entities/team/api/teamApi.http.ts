import { apiClient } from '@/shared/api'
import type { TeamApi } from './types'

export const teamHttpApi: TeamApi = {
  list: () => apiClient.get('/teams'),
  create: (name) => apiClient.post('/teams', { name }),
  remove: (teamId) => apiClient.delete(`/teams/${teamId}`),

  listMembers: (teamId) => apiClient.get(`/teams/${teamId}/members`),
  updateMemberRole: (teamId, memberId, role) => apiClient.patch(`/teams/${teamId}/members/${memberId}`, { role }),
  removeMember: (teamId, memberId) => apiClient.delete(`/teams/${teamId}/members/${memberId}`),

  listInvites: (teamId) => apiClient.get(`/teams/${teamId}/invites`),
  createInvite: (teamId, role) => apiClient.post(`/teams/${teamId}/invites`, { role }),
  revokeInvite: (teamId, inviteId) => apiClient.delete(`/teams/${teamId}/invites/${inviteId}`),
}
