import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { resetIntegrationSession } from '@/shared/api'
import { useTeamStore } from './teamStore'
import type { Team } from './types'

const { list, create, remove } = vi.hoisted(() => ({ list: vi.fn(), create: vi.fn(), remove: vi.fn() }))
vi.mock('../api', () => ({ teamApi: { list, create, remove } }))
const team = (id: string): Team => ({ id, name: id, myRole: 'owner', membersCount: 1, avatarUrl: null })

describe('team mutation isolation between sessions', () => {
  beforeEach(() => { vi.resetAllMocks(); setActivePinia(createPinia()) })
  it('does not add or select a previous account team after session reset', async () => {
    let finish!: (value: Team) => void
    create.mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    const store = useTeamStore()
    const creating = store.createTeam('private team')
    resetIntegrationSession()
    store.teams = [team('new account')]
    store.switchTeam('new account')
    finish(team('private team'))
    await creating
    expect(store.teams.map((item) => item.name)).toEqual(['new account'])
    expect(store.currentTeamId).toBe('new account')
  })

  it('does not remove a new account team when an earlier deletion completes', async () => {
    let finish!: () => void
    remove.mockImplementation(() => new Promise<void>((resolve) => { finish = resolve }))
    const store = useTeamStore()
    store.teams = [team('shared')]
    const deleting = store.deleteTeam('shared')
    resetIntegrationSession()
    store.teams = [team('shared')]
    store.switchTeam('shared')
    finish()
    await deleting
    expect(store.currentTeam?.id).toBe('shared')
  })
})
