<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/entities/user'
import { getRoleLabel, useTeamStore, type Team, type TeamRole } from '@/entities/team'
import { clearPendingInvite, rememberInvite } from '@/features/accept-invite/model/pendingInvite'
import { apiClient, ApiError, sessionGeneration } from '@/shared/api'
import { RouteName } from '@/shared/config'
import { BaseButton, BaseCard } from '@/shared/ui'

interface InvitePreview { team: { id: string; name: string }; role: TeamRole; expiresAt: string }
const route = useRoute()
const router = useRouter()
const user = useUserStore()
const teams = useTeamStore()
const preview = ref<InvitePreview | null>(null)
const loading = ref(false)
const joining = ref(false)
const error = ref<string | null>(null)
let token: string | null = null
let generation = 0

watch(() => route.hash, async (hash) => {
  const current = ++generation
  token = rememberInvite(hash)
  if (hash) { await router.replace({ hash: '' }); return }
  preview.value = null
  error.value = null
  if (!token) { error.value = 'invite.join.invalid'; return }
  loading.value = true
  try {
    const result = await apiClient.post<InvitePreview>('/invites/preview', { token })
    if (current === generation) preview.value = result
  } catch (cause) {
    if (current === generation) error.value = cause instanceof ApiError && [404, 409, 410].includes(cause.status) ? 'invite.join.invalid' : 'invite.join.loadError'
  } finally { if (current === generation) loading.value = false }
}, { immediate: true })

async function accept() {
  if (!token || !preview.value || joining.value) return
  const account = sessionGeneration.value
  const request = generation
  const invitationToken = token
  joining.value = true
  error.value = null
  try {
    const team = await apiClient.post<Team>('/invites/accept', { token: invitationToken })
    if (account !== sessionGeneration.value || request !== generation) return
    await teams.refresh()
    if (account !== sessionGeneration.value || request !== generation) return
    teams.switchTeam(team.id)
    clearPendingInvite()
    await router.replace({ name: RouteName.TeamProjects })
  } catch (cause) {
    if (account !== sessionGeneration.value || request !== generation) return
    error.value = cause instanceof ApiError && [404, 409, 410].includes(cause.status) ? 'invite.join.invalid' : 'invite.join.acceptError'
  } finally { if (request === generation) joining.value = false }
}
onUnmounted(() => {
  generation += 1
  token = null
  preview.value = null
})
</script>

<template>
  <div class="container invitation-page">
    <BaseCard padding="lg" class="invitation-page__card">
      <h1>{{ $t('invite.join.title') }}</h1>
      <p v-if="loading">{{ $t('common.loading') }}</p>
      <p v-if="error" role="alert" class="invitation-page__error">{{ $t(error) }}</p>
      <template v-if="preview">
        <p>{{ $t('invite.join.team', { team: preview.team.name }) }}</p>
        <p>{{ $t('invite.join.role', { role: getRoleLabel(preview.role) }) }}</p>
        <p class="invitation-page__hint">{{ $t('invite.join.scope') }}</p>
        <BaseButton v-if="user.isAuthenticated" :loading="joining" @click="accept">{{ $t('invite.join.accept') }}</BaseButton>
        <div v-else class="invitation-page__actions">
          <p>{{ $t('invite.join.signIn') }}</p>
          <RouterLink :to="{ name: RouteName.Login, query: { redirect: '/invite/team' } }">{{ $t('auth.submit') }}</RouterLink>
          <RouterLink :to="{ name: RouteName.Register, query: { redirect: '/invite/team' } }">{{ $t('auth.registerSubmit') }}</RouterLink>
        </div>
      </template>
      <RouterLink :to="{ name: user.isAuthenticated ? RouteName.TeamProjects : RouteName.Login }" class="invitation-page__back">{{ $t('invite.join.back') }}</RouterLink>
    </BaseCard>
  </div>
</template>

<style scoped>
.invitation-page { display: flex; flex: 1; justify-content: center; align-items: center; }
.invitation-page__card { width: min(560px, 100%); display: flex; flex-direction: column; gap: 20px; }
h1 { font-family: var(--font-display); font-size: 28px; }
p { line-height: 1.5; }
.invitation-page__hint, .invitation-page__back { color: var(--color-text-muted); font-size: 14px; }
.invitation-page__actions { display: flex; flex-wrap: wrap; gap: 18px; }
.invitation-page__actions p { flex-basis: 100%; }
a { text-decoration: underline; text-underline-offset: 3px; }
.invitation-page__error { color: var(--color-danger); }
</style>
