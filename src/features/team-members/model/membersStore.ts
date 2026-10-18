import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import { canManageMembers, canManageMember, teamApi, useTeamStore, type TeamMember, type TeamRole } from '@/entities/team'

const ROLE_ORDER = { owner: 0, admin: 1, member: 2, viewer: 3 } as const

export const useMembersStore = defineStore('team-members', () => {
  const teamStore = useTeamStore()

  let generation = 0
  const members = ref<TeamMember[]>([])
  const isLoading = ref(false)
  const pending = ref(new Set<string>())
  const error = ref<string | null>(null)

  const canManage = computed(() => (teamStore.currentTeam ? canManageMembers(teamStore.currentTeam.myRole) : false))

  const sorted = computed(() =>
    [...members.value].sort((a, b) => ROLE_ORDER[a.role] - ROLE_ORDER[b.role] || a.name.localeCompare(b.name)),
  )

  async function load(teamId: string) {
    const request = ++generation
    isLoading.value = true
    error.value = null
    try {
      const result = await teamApi.listMembers(teamId)
      if (request !== generation) return
      members.value = result
      teamStore.setMembersCount(teamId, members.value.length)
    } catch {
      if (request === generation) error.value = 'members.errors.load'
    } finally {
      if (request === generation) isLoading.value = false
    }
  }

  async function run(memberId: string, task: (teamId: string) => Promise<void>, failText: string) {
    const teamId = teamStore.currentTeamId
    const member = members.value.find((item) => item.id === memberId)
    if (!teamId || !member || !teamStore.currentTeam || !canManageMember(teamStore.currentTeam.myRole, member.role, member.isYou)) return
    const request = generation
    pending.value.add(memberId)
    error.value = null
    try {
      await task(teamId)
    } catch {
      if (request === generation) error.value = failText
    } finally {
      if (request === generation) pending.value.delete(memberId)
    }
  }

  function setRole(memberId: string, role: TeamRole) {
    const current = generation
    return run(memberId, async (teamId) => {
      const updated = await teamApi.updateMemberRole(teamId, memberId, role)
      if (current !== generation || teamId !== teamStore.currentTeamId) return
      members.value = members.value.map((member) => member.id === memberId ? updated : member)
    }, 'members.errors.role')
  }

  function remove(memberId: string) {
    const current = generation
    return run(memberId, async (teamId) => {
      await teamApi.removeMember(teamId, memberId)
      if (current !== generation || teamId !== teamStore.currentTeamId) return
      members.value = members.value.filter((member) => member.id !== memberId)
      teamStore.setMembersCount(teamId, members.value.length)
    }, 'members.errors.remove')
  }

  watch(
    () => teamStore.currentTeamId,
    (teamId) => {
      generation += 1
      members.value = []
      pending.value.clear()
      error.value = null
      isLoading.value = false
      if (teamId) load(teamId)
    },
    { immediate: true, flush: 'sync' },
  )

  return { members: sorted, isLoading, pending, error, canManage, setRole, remove }
})
