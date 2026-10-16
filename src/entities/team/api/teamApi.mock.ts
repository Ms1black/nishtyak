import { ApiError, MOCK_TEAM_PHOTOS, MOCK_USER_PHOTOS } from '@/shared/api'
import { createTeamAvatar, createUserAvatar, delay } from '@/shared/lib'
import type { Team, TeamInvite, TeamMember, TeamRole } from '../model/types'
import type { TeamApi } from './types'

const DAY_MS = 24 * 60 * 60 * 1000
const INVITE_TTL_DAYS = 7
const ME = { id: 'u-1', name: 'Алексей Ковалёв', email: 'a.kovalev@nishtyak.dev' }

const daysAgo = (days: number) => new Date(Date.now() - days * DAY_MS).toISOString()

interface TeamRecord {
  id: string
  name: string
  members: TeamMember[]
  invites: TeamInvite[]
}

const member = (id: string, name: string, email: string, role: TeamMember['role'], joined: number): TeamMember => ({
  id,
  name,
  email,
  role,
  joinedAt: daysAgo(joined),
  isYou: id === ME.id,
  avatarUrl: MOCK_USER_PHOTOS[id] ?? createUserAvatar(id),
})

const teams: TeamRecord[] = [
  {
    id: 't-nishtyak',
    name: 'Ништяк',
    members: [
      member(ME.id, ME.name, ME.email, 'owner', 40),
      member('u-2', 'Мария Соколова', 'm.sokolova@misis.ru', 'admin', 32),
      member('u-3', 'Илья Гордеев', 'gordeev@bmstu.ru', 'member', 21),
      member('u-4', 'Дарья Лебедева', 'd.lebedeva@misis.ru', 'member', 9),
      member('u-5', 'Олег Миронов', 'oleg.mironov@gmail.com', 'viewer', 2),
    ],
    invites: [],
  },
  {
    id: 't-misis',
    name: 'MISIS Lab',
    members: [
      member('u-6', 'Пётр Андреев', 'p.andreev@misis.ru', 'owner', 120),
      member(ME.id, ME.name, ME.email, 'member', 60),
      member('u-2', 'Мария Соколова', 'm.sokolova@misis.ru', 'admin', 90),
    ],
    invites: [],
  },
]

function makeInvite(role: TeamRole, token: string, createdDaysAgo = 0): TeamInvite {
  const createdAt = Date.now() - createdDaysAgo * DAY_MS
  return {
    id: `inv-${token}`,
    url: `https://app.example.com/invite/team/${token}`,
    role,
    createdAt: new Date(createdAt).toISOString(),
    expiresAt: new Date(createdAt + INVITE_TTL_DAYS * DAY_MS).toISOString(),
  }
}

teams[0]!.invites = [makeInvite('member', 'abc123'), makeInvite('member', 'afksdokfodk', 6)]

function getTeam(teamId: string): TeamRecord {
  const team = teams.find((t) => t.id === teamId && t.members.some((m) => m.id === ME.id))
  if (!team) throw new ApiError(404, 'RESOURCE_NOT_FOUND')
  return team
}

function myRole(team: TeamRecord) {
  return team.members.find((m) => m.id === ME.id)!.role
}

function requireManager(team: TeamRecord) {
  if (myRole(team) !== 'owner' && myRole(team) !== 'admin') throw new ApiError(403, 'FORBIDDEN')
}

function toTeam(team: TeamRecord): Team {
  return {
    id: team.id,
    name: team.name,
    myRole: myRole(team),
    membersCount: team.members.length,
    avatarUrl: MOCK_TEAM_PHOTOS[team.id] ?? createTeamAvatar(team.id),
  }
}

function activeInvites(team: TeamRecord): TeamInvite[] {
  team.invites = team.invites.filter((i) => Date.parse(i.expiresAt) > Date.now())
  return team.invites
}

export const teamMockApi: TeamApi = {
  async list() {
    await delay(200)
    return teams.filter((t) => t.members.some((m) => m.id === ME.id)).map(toTeam)
  },

  async create(name) {
    await delay(400)
    const team: TeamRecord = {
      id: `t-${crypto.randomUUID()}`,
      name,
      members: [member(ME.id, ME.name, ME.email, 'owner', 0)],
      invites: [makeInvite('member', Math.random().toString(36).slice(2, 10))],
    }
    teams.push(team)
    return toTeam(team)
  },

  async remove(teamId) {
    await delay(400)
    const team = getTeam(teamId)
    if (myRole(team) !== 'owner') throw new ApiError(403, 'FORBIDDEN')
    teams.splice(teams.indexOf(team), 1)
  },

  async listMembers(teamId) {
    await delay(250)
    return structuredClone(getTeam(teamId).members)
  },

  async updateMemberRole(teamId, memberId, role) {
    await delay(250)
    const team = getTeam(teamId)
    requireManager(team)
    const target = team.members.find((m) => m.id === memberId)
    if (!target || target.role === 'owner') throw new ApiError(403, 'FORBIDDEN')
    target.role = role
    return { ...target }
  },

  async removeMember(teamId, memberId) {
    await delay(300)
    const team = getTeam(teamId)
    requireManager(team)
    const target = team.members.find((m) => m.id === memberId)
    if (!target || target.role === 'owner' || target.isYou) throw new ApiError(403, 'FORBIDDEN')
    team.members = team.members.filter((m) => m.id !== memberId)
  },

  async listInvites(teamId) {
    await delay(250)
    return structuredClone(activeInvites(getTeam(teamId)))
  },

  async createInvite(teamId, role) {
    await delay(500)
    const team = getTeam(teamId)
    const invite = makeInvite(role, Math.random().toString(36).slice(2, 10))
    team.invites = [invite, ...activeInvites(team)]
    return { ...invite }
  },

  async revokeInvite(teamId, inviteId) {
    await delay(250)
    const team = getTeam(teamId)
    team.invites = activeInvites(team).filter((i) => i.id !== inviteId)
  },
}
