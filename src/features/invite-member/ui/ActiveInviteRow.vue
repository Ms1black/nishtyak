<script setup lang="ts">
import { getRoleLabel, type TeamInvite } from '@/entities/team'
import { daysLeft, formatDaysLeft } from '../model/inviteStore'

defineProps<{ invite: TeamInvite; revoking?: boolean }>()
const emit = defineEmits<{ revoke: [id: string] }>()
</script>

<template>
  <li class="link-row">
    <span class="link-row__url">{{ invite.url }}</span>
    <span v-if="invite.role !== 'member'" class="link-row__role">{{ getRoleLabel(invite.role) }}</span>
    <span class="link-row__expires">{{ $t('invite.activeExpires', { days: formatDaysLeft(daysLeft(invite)) }) }}</span>
    <button type="button" class="link-row__revoke" :disabled="revoking" @click="emit('revoke', invite.id)">
      {{ revoking ? $t('invite.revoking') : $t('invite.revoke') }}
    </button>
  </li>
</template>

<style scoped>
.link-row {
  display: flex;
  align-items: center;
  gap: 16px;
  min-height: 68px;
  padding: 12px 20px 12px 20px;
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-lg);
}

.link-row__url {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  font-size: 15px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.link-row__role {
  color: var(--color-text-secondary);
  font-size: 13px;
  white-space: nowrap;
}

.link-row__expires {
  display: inline-flex;
  align-items: center;
  height: 28px;
  padding: 0 12px;
  border-radius: var(--radius-pill);
  background: var(--color-surface-raised);
  color: var(--color-text-muted);
  font-size: 13px;
  white-space: nowrap;
}

.link-row__revoke {
  color: #ff4d4d;
  font-size: 15px;
  white-space: nowrap;
  transition: opacity var(--transition);
}

.link-row__revoke:hover:not(:disabled) {
  opacity: 0.8;
}

.link-row__revoke:disabled {
  opacity: 0.5;
}

@media (max-width: 640px) {
  .link-row {
    flex-wrap: wrap;
  }

  .link-row__url {
    flex-basis: 100%;
  }
}
</style>
