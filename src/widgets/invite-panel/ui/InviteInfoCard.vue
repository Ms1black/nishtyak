<script setup lang="ts">
import { useTeamStore } from '@/entities/team'
import { BaseCard, BaseIcon } from '@/shared/ui'

const teamStore = useTeamStore()
</script>

<template>
  <BaseCard tag="aside" padding="lg" class="info">
    <h2 class="info__title">{{ $t('invite.info.title') }}</h2>
    <p class="info__text">{{ $t('invite.info.text', { team: teamStore.currentTeam?.name ?? '…' }) }}</p>
    <div class="info__note">
      <BaseIcon name="lock" :size="22" />
      <p>{{ $t('invite.info.note') }}</p>
    </div>
  </BaseCard>
</template>

<style scoped>
.info {
  align-self: start;
}

.info__title {
  font-family: var(--font-display);
  font-size: 24px;
  font-weight: 500;
  line-height: 1.2;
}

.info__text {
  margin-top: 28px;
  color: var(--color-text-secondary);
  font-size: 15px;
  line-height: 1.5;
}

.info__note {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  margin-top: 24px;
  padding: 16px;
  border-radius: var(--radius-md);
  background: var(--color-surface-raised);
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.45;
}

.info__note :deep(.icon) {
  color: var(--color-text);
}
</style>
