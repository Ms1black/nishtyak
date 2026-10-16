import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import { teamApi, useTeamStore, type TeamInvite, type TeamRole } from '@/entities/team'
import { t } from '@/shared/i18n'
import { isStandaloneWorkspace } from '@/shared/api'

const DAY_MS = 24 * 60 * 60 * 1000

export function daysLeft(invite: TeamInvite): number {
  return Math.max(1, Math.ceil((Date.parse(invite.expiresAt) - Date.now()) / DAY_MS))
}

export function formatDaysLeft(days: number): string { return t('units.days', days) }

export const useInviteStore = defineStore('team-invites', () => {
  const teamStore = useTeamStore()
  const invites = ref<TeamInvite[]>([])
  const isLoading = ref(false)
  const isCreating = ref(false)
  const isRotating = ref(false)
  const notice = ref<string | null>(null)
  const revoking = ref(new Set<string>())
  const error = ref<string | null>(null)
  let noticeTimer: ReturnType<typeof setTimeout> | undefined
  let request = 0

  const canManage = computed(() => !isStandaloneWorkspace.value && teamStore.canManage)
  const current = computed(() => invites.value.find((invite) => invite.url) ?? null)
  const others = computed(() => invites.value.filter((invite) => invite.id !== current.value?.id))
  const expiresText = computed(() => current.value ? t('invite.expires', { days: formatDaysLeft(daysLeft(current.value)) }) : t('invite.createHint'))

  function notify(text: string) {
    notice.value = text
    clearTimeout(noticeTimer)
    noticeTimer = setTimeout(() => (notice.value = null), 3000)
  }

  async function load(teamId: string) {
    const generation = ++request
    isLoading.value = true
    error.value = null
    try {
      const result = await teamApi.listInvites(teamId)
      if (generation === request) invites.value = result
    } catch {
      if (generation === request) error.value = 'invite.errors.load'
    } finally {
      if (generation === request) isLoading.value = false
    }
  }

  async function add(role: TeamRole) {
    const teamId = teamStore.currentTeamId
    if (!teamId || !canManage.value || isCreating.value) return
    const generation = request
    isCreating.value = true
    error.value = null
    try {
      const invite = await teamApi.createInvite(teamId, role)
      if (generation !== request) return
      invites.value = [invite, ...invites.value]
      notify('invite.added')
    } catch {
      if (generation === request) error.value = 'invite.errors.create'
    } finally {
      if (generation === request) isCreating.value = false
    }
  }

  async function rotate() {
    const teamId = teamStore.currentTeamId
    const previous = current.value
    if (!teamId || !previous || !canManage.value || isRotating.value) return
    const generation = request
    isRotating.value = true
    error.value = null
    try {
      const invite = await teamApi.createInvite(teamId, previous.role)
      if (generation !== request) return
      // Keep the new link visible even if revoking the old link fails.
      invites.value = [invite, ...invites.value]
      await teamApi.revokeInvite(teamId, previous.id)
      if (generation !== request) return
      invites.value = invites.value.filter((i) => i.id !== previous.id)
      notify('invite.rotated')
    } catch {
      if (generation === request) error.value = 'invite.errors.rotate'
    } finally {
      if (generation === request) isRotating.value = false
    }
  }

  async function revoke(inviteId: string) {
    const teamId = teamStore.currentTeamId
    if (!teamId || !canManage.value) return
    const generation = request
    revoking.value.add(inviteId)
    try {
      await teamApi.revokeInvite(teamId, inviteId)
      if (generation === request) invites.value = invites.value.filter((i) => i.id !== inviteId)
    } catch {
      if (generation === request) error.value = 'invite.errors.revoke'
    } finally {
      if (generation === request) revoking.value.delete(inviteId)
    }
  }

  watch(() => [teamStore.currentTeamId, canManage.value] as const, ([teamId, manager]) => {
    request += 1
    clearTimeout(noticeTimer)
    invites.value = []
    error.value = notice.value = null
    isLoading.value = isCreating.value = isRotating.value = false
    revoking.value.clear()
    if (teamId && (manager || isStandaloneWorkspace.value)) void load(teamId)
  }, { immediate: true, flush: 'sync' })

  return { invites, current, others, canManage, isLoading, isCreating, isRotating, revoking, error, notice, expiresText, add, rotate, revoke }
})
