export type TeamRole = 'member' | 'admin' | 'viewer'

export type TeamMemberRole = 'owner' | TeamRole

export interface Team {
  id: string
  name: string
  personal?: boolean
  myRole: TeamMemberRole
  membersCount: number
  avatarUrl: string | null
}

export interface TeamMember {
  id: string
  name: string
  email: string
  avatarUrl: string | null
  role: TeamMemberRole
  joinedAt: string
  isYou: boolean
}

export interface TeamInvite {
  id: string
  url: string | null
  role: TeamRole
  expiresAt: string
  createdAt: string
}
