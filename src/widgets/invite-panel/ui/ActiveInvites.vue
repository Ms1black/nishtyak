<script setup lang="ts">
import { ActiveInviteRow, useInviteStore } from '@/features/invite-member'

const inviteStore = useInviteStore()
</script>

<template>
  <section v-if="inviteStore.others.length" class="active-invites">
    <h2 class="active-invites__title">{{ $t('invite.activeTitle') }}</h2>
    <TransitionGroup tag="ul" name="row" class="active-invites__list">
      <ActiveInviteRow
        v-for="invite in inviteStore.others"
        :key="invite.id"
        :invite="invite"
        :revoking="inviteStore.revoking.has(invite.id)"
        @revoke="inviteStore.revoke"
      />
    </TransitionGroup>
  </section>
</template>

<style scoped>
.active-invites__title {
  padding-left: 10px;
  font-family: var(--font-display);
  font-size: 24px;
  font-weight: 500;
}

.active-invites__list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 28px;
}

.row-leave-active {
  transition:
    opacity 200ms ease,
    transform 200ms ease;
}

.row-leave-to {
  opacity: 0;
  transform: translateX(12px);
}
</style>
