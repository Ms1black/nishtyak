import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import { teamApi } from '../api'
import type { Team } from './types'
import { getIntegrationSession, sessionGeneration } from '@/shared/api'

const STORAGE_KEY = 'nishtyak:current-team'

function readStoredTeamId(): string | null {
  try { return localStorage.getItem(STORAGE_KEY) } catch { return null }
}

export const useTeamStore = defineStore('team', () => {
  const teams = ref<Team[]>([])
  const currentTeamId = ref<string | null>(readStoredTeamId())
  const currentTeam = computed(() => teams.value.find((t) => t.id === currentTeamId.value) ?? null)
  const canEditProjects = computed(() => !!currentTeam.value && currentTeam.value.myRole !== 'viewer')
  const canManage = computed(() => currentTeam.value?.myRole === 'owner' || currentTeam.value?.myRole === 'admin')
  let loading: Promise<void> | null = null
  let request = 0

  function init(): Promise<void> {
    const current = request
    loading ??= (async () => {
      try {
        const session = await getIntegrationSession()
        if (current !== request) return
        if (session && (session.mode === 'local' || session.mode === 'preview')) {
          teams.value = [{ ...session.team, myRole: 'owner', membersCount: 1, avatarUrl: null }]
          switchTeam(session.team.id)
          return
        }
        const result = await teamApi.list()
        if (current !== request) return
        teams.value = result
        if (!currentTeam.value) switchTeam(result[0]?.id ?? null)
      } catch (error) {
        if (current === request) loading = null
        throw error
      }
    })()
    return loading
  }

  function switchTeam(id: string | null) {
    currentTeamId.value = id
    try {
      if (id) localStorage.setItem(STORAGE_KEY, id)
      else localStorage.removeItem(STORAGE_KEY)
    } catch { /* Storage may be disabled in private browsing. */ }
  }

  function reset() {
    request += 1
    loading = null
    teams.value = []
    switchTeam(null)
  }

  async function refresh() {
    request += 1
    loading = null
    await init()
  }

  async function createTeam(name: string): Promise<Team> {
    const generation = request
    const team = await teamApi.create(name)
    if (generation !== request) return team
    teams.value = [...teams.value, team]
    switchTeam(team.id)
    return team
  }

  async function deleteTeam(id: string) {
    const generation = request
    await teamApi.remove(id)
    if (generation !== request) return
    teams.value = teams.value.filter((t) => t.id !== id)
    if (currentTeamId.value === id) switchTeam(teams.value[0]?.id ?? null)
  }

  function setMembersCount(id: string, count: number) {
    teams.value = teams.value.map((t) => (t.id === id ? { ...t, membersCount: count } : t))
  }

  watch(sessionGeneration, reset, { flush: 'sync' })
  return { teams, currentTeamId, currentTeam, canEditProjects, canManage, init, refresh, reset, switchTeam, createTeam, deleteTeam, setMembersCount }
})
