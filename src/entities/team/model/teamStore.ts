import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { teamApi } from '../api'
import type { Team } from './types'

const STORAGE_KEY = 'nishtyak:current-team'

function readStoredTeamId(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY)
  } catch {
    return null
  }
}

export const useTeamStore = defineStore('team', () => {
  const teams = ref<Team[]>([])
  const currentTeamId = ref<string | null>(readStoredTeamId())

  const currentTeam = computed(() => teams.value.find((t) => t.id === currentTeamId.value) ?? null)

  let loading: Promise<void> | null = null

  function init(): Promise<void> {
    loading ??= (async () => {
      teams.value = await teamApi.list()
      if (!currentTeam.value) switchTeam(teams.value[0]?.id ?? null)
    })()
    return loading
  }

  function switchTeam(id: string | null) {
    currentTeamId.value = id
    try {
      if (id) localStorage.setItem(STORAGE_KEY, id)
      else localStorage.removeItem(STORAGE_KEY)
    } catch {
    }
  }

  async function createTeam(name: string): Promise<Team> {
    const team = await teamApi.create(name)
    teams.value = [...teams.value, team]
    switchTeam(team.id)
    return team
  }

  async function deleteTeam(id: string) {
    await teamApi.remove(id)
    teams.value = teams.value.filter((t) => t.id !== id)
    if (currentTeamId.value === id) switchTeam(teams.value[0]?.id ?? null)
  }

  function setMembersCount(id: string, count: number) {
    teams.value = teams.value.map((t) => (t.id === id ? { ...t, membersCount: count } : t))
  }

  return { teams, currentTeamId, currentTeam, init, switchTeam, createTeam, deleteTeam, setMembersCount }
})
