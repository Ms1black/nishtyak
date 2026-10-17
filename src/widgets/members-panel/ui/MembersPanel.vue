<script setup lang="ts">
import { RouterLink } from 'vue-router'
import { useTeamStore } from '@/entities/team'
import { MemberRow, useMembersStore } from '@/features/team-members'
import { RouteName } from '@/shared/config'
import { BaseIcon } from '@/shared/ui'

const teamStore = useTeamStore()
const membersStore = useMembersStore()
</script>

<template>
  <section class="members" aria-labelledby="members-title">
    <header class="members__head">
      <h2 id="members-title" class="members__title">{{ $t('members.title', { team: teamStore.currentTeam?.name ?? '…' }) }}</h2>
      <RouterLink v-if="membersStore.canManage" :to="{ name: RouteName.TeamInvite }" class="members__invite">
        <BaseIcon name="plus" :size="18" />
        {{ $t('members.invite') }}
      </RouterLink>
    </header>

    <p v-if="membersStore.error" class="members__error" role="alert">{{ $t(membersStore.error) }}</p>

    <div class="members__table">
      <div class="members__columns" aria-hidden="true">
        <span />
        <span>{{ $t('members.columns.member') }}</span>
        <span>{{ $t('members.columns.joined') }}</span>
        <span>{{ $t('members.columns.role') }}</span>
        <span />
      </div>
      <ul v-if="membersStore.members.length">
        <MemberRow
          v-for="member in membersStore.members"
          :key="member.id"
          :member="member"
          :can-manage="membersStore.canManage"
          :pending="membersStore.pending.has(member.id)"
          @set-role="membersStore.setRole"
          @remove="membersStore.remove"
        />
      </ul>
      <p v-else class="members__empty">{{ membersStore.isLoading ? $t('common.loading') : $t('members.empty') }}</p>
    </div>
  </section>
</template>

<style scoped>
.members__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.members__title {
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 500;
}

.members__invite {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 40px;
  padding: 0 18px;
  border-radius: var(--radius-pill);
  background: var(--color-inverse-bg);
  color: var(--color-inverse-text);
  font-size: 14px;
  font-weight: 500;
  white-space: nowrap;
}

.members__error {
  margin-top: 16px;
  color: var(--color-danger);
  font-size: 13px;
}

.members__table {
  margin-top: 24px;
  border-radius: var(--radius-xl);
  background: var(--color-surface);
}

.members__columns {
  display: grid;
  grid-template-columns: 40px minmax(0, 1fr) 170px 200px 90px;
  align-items: center;
  gap: 16px;
  height: 46px;
  padding: 0 20px;
  border-radius: var(--radius-xl) var(--radius-xl) 0 0;
  background: var(--color-surface-header);
  color: var(--color-text-secondary);
  font-size: 13px;
}

.members__empty {
  padding: 40px 20px;
  color: var(--color-text-muted);
  text-align: center;
}

.members__table :deep(.member:first-child) {
  border-top: 0;
}

@media (max-width: 760px) {
  .members__columns {
    display: none;
  }
}
</style>
