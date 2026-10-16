import type { Team, TeamInvite, TeamMember, TeamRole } from '../model/types'

export interface TeamApi {
  list(): Promise<Team[]>
  create(name: string): Promise<Team>
  remove(teamId: string): Promise<void>

  listMembers(teamId: string): Promise<TeamMember[]>
  updateMemberRole(teamId: string, memberId: string, role: TeamRole): Promise<TeamMember>
  removeMember(teamId: string, memberId: string): Promise<void>

  listInvites(teamId: string): Promise<TeamInvite[]>
  createInvite(teamId: string, role: TeamRole): Promise<TeamInvite>
  revokeInvite(teamId: string, inviteId: string): Promise<void>
}
