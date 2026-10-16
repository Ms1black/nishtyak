import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import { teamApi, useTeamStore, type TeamInvite, type TeamRole } from '@/entities/team'
import { t } from '@/shared/i18n'

const DAY_MS = 24 * 60 * 60 * 1000

export function daysLeft(invite: TeamInvite): number {
  return Math.max(1, Math.ceil((Date.parse(invite.expiresAt) - Date.now()) / DAY_MS))
}

export function formatDaysLeft(days: number): string {
  return t('units.days', days)
}

export const useInviteStore = defineStore('team-invites', () => {
  const teamStore = useTeamStore()

  const invites = ref<TeamInvite[]>([])
  const isLoading = ref(false)
  const isCreating = ref(false)
  const isRotating = ref(false)
  const notice = ref<string | null>(null)
  let noticeTimer: ReturnType<typeof setTimeout> | undefined
  const revoking = ref(new Set<string>())
  const error = ref<string | null>(null)

  const current = computed(() => invites.value[0] ?? null)
  const others = computed(() => invites.value.slice(1))
  const expiresText = computed(() =>
    current.value
      ? t('invite.expires', { days: formatDaysLeft(daysLeft(current.value)) })
      : '',
  )

  function notify(text: string) {
    notice.value = text
    clearTimeout(noticeTimer)
    noticeTimer = setTimeout(() => (notice.value = null), 3000)
  }

  async function load(teamId: string) {
    isLoading.value = true
    error.value = null
    try {
      invites.value = await teamApi.listInvites(teamId)
      if (!invites.value.length) await add(current.value?.role ?? 'member', { silent: true })
    } catch {
      error.value = 'invite.errors.load'
    } finally {
      isLoading.value = false
    }
  }

  async function add(role: TeamRole, { silent = false } = {}) {
    const teamId = teamStore.currentTeamId
    if (!teamId) return
    isCreating.value = true
    error.value = null
    try {
      const invite = await teamApi.createInvite(teamId, role)
      invites.value = [invite, ...invites.value]
      if (!silent && invites.value.length > 1) notify('invite.added')
    } catch {
      error.value = 'invite.errors.create'
    } finally {
      isCreating.value = false
    }
  }

  async function rotate() {
    const teamId = teamStore.currentTeamId
    const previous = current.value
    if (!teamId || !previous) return
    isRotating.value = true
    error.value = null
    try {
      const invite = await teamApi.createInvite(teamId, previous.role)
      await teamApi.revokeInvite(teamId, previous.id)
      invites.value = [invite, ...invites.value.filter((i) => i.id !== previous.id)]
      notify('invite.rotated')
    } catch {
      error.value = 'invite.errors.rotate'
    } finally {
      isRotating.value = false
    }
  }

  async function revoke(inviteId: string) {
    const teamId = teamStore.currentTeamId
    if (!teamId) return
    revoking.value.add(inviteId)
    try {
      await teamApi.revokeInvite(teamId, inviteId)
      invites.value = invites.value.filter((i) => i.id !== inviteId)
    } catch {
      error.value = 'invite.errors.revoke'
    } finally {
      revoking.value.delete(inviteId)
    }
  }

  watch(
    () => teamStore.currentTeamId,
    (teamId) => {
      if (teamId) load(teamId)
    },
    { immediate: true },
  )

  return {
    invites,
    current,
    others,
    isLoading,
    isCreating,
    isRotating,
    revoking,
    error,
    notice,
    expiresText,
    add,
    rotate,
    revoke,
  }
})
