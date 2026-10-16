import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import { canManageMembers, teamApi, useTeamStore, type TeamMember, type TeamRole } from '@/entities/team'

const ROLE_ORDER = { owner: 0, admin: 1, member: 2, viewer: 3 } as const

export const useMembersStore = defineStore('team-members', () => {
  const teamStore = useTeamStore()

  const members = ref<TeamMember[]>([])
  const isLoading = ref(false)
  const pending = ref(new Set<string>())
  const error = ref<string | null>(null)

  const canManage = computed(() => (teamStore.currentTeam ? canManageMembers(teamStore.currentTeam.myRole) : false))

  const sorted = computed(() =>
    [...members.value].sort((a, b) => ROLE_ORDER[a.role] - ROLE_ORDER[b.role] || a.name.localeCompare(b.name)),
  )

  async function load(teamId: string) {
    isLoading.value = true
    error.value = null
    try {
      members.value = await teamApi.listMembers(teamId)
      teamStore.setMembersCount(teamId, members.value.length)
    } catch {
      error.value = 'members.errors.load'
    } finally {
      isLoading.value = false
    }
  }

  async function run(memberId: string, task: (teamId: string) => Promise<void>, failText: string) {
    const teamId = teamStore.currentTeamId
    if (!teamId) return
    pending.value.add(memberId)
    error.value = null
    try {
      await task(teamId)
    } catch {
      error.value = failText
    } finally {
      pending.value.delete(memberId)
    }
  }

  const setRole = (memberId: string, role: TeamRole) =>
    run(
      memberId,
      async (teamId) => {
        const updated = await teamApi.updateMemberRole(teamId, memberId, role)
        members.value = members.value.map((m) => (m.id === memberId ? updated : m))
      },
      'members.errors.role',
    )

  const remove = (memberId: string) =>
    run(
      memberId,
      async (teamId) => {
        await teamApi.removeMember(teamId, memberId)
        members.value = members.value.filter((m) => m.id !== memberId)
        teamStore.setMembersCount(teamId, members.value.length)
      },
      'members.errors.remove',
    )

  watch(
    () => teamStore.currentTeamId,
    (teamId) => {
      if (teamId) load(teamId)
    },
    { immediate: true },
  )

  return { members: sorted, isLoading, pending, error, canManage, setRole, remove }
})
