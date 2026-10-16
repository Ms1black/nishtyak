<script setup lang="ts">
import { ref } from 'vue'
import { getTeamRoleOptions, type TeamRole } from '@/entities/team'
import { storeToRefs } from 'pinia'
import { InviteLinkField, useInviteStore } from '@/features/invite-member'
import { BaseButton, BaseCard, BaseSelect } from '@/shared/ui'

const inviteStore = useInviteStore()
const { current: invite, isLoading, isCreating, isRotating, error, notice, expiresText } = storeToRefs(inviteStore)

const role = ref<TeamRole | null>('member')
</script>

<template>
  <BaseCard padding="lg" class="invite">
    <h2 class="invite__title">{{ $t('invite.title') }}</h2>

    <div class="invite__link">
      <InviteLinkField
        :url="invite?.url ?? null"
        :loading="isLoading"
        :regenerating="isRotating"
        @regenerate="inviteStore.rotate()"
      />
      <p class="invite__expires" :class="{ 'is-error': !!error }" aria-live="polite">
        {{ error ? $t(error) : notice ? $t(notice) : expiresText }}
      </p>
    </div>

    <div class="invite__role">
      <h3 id="invite-role-label" class="invite__label">{{ $t('invite.roleLabel') }}</h3>
      <BaseSelect
        v-model="role"
        :options="getTeamRoleOptions()"
        icon="user"
        aria-labelledby="invite-role-label"
        :disabled="isLoading"
      />
    </div>

    <div class="invite__footer">
      <BaseButton
        size="lg"
        class="invite__done"
        :loading="isCreating"
        :disabled="isLoading || isRotating || !role"
        @click="role && inviteStore.add(role)"
      >
        {{ $t('invite.add') }}
      </BaseButton>
    </div>
  </BaseCard>
</template>

<style scoped>
.invite__title {
  font-family: var(--font-display);
  font-size: 24px;
  font-weight: 500;
  line-height: 1.2;
}

.invite__link {
  margin-top: 48px;
}

.invite__expires {
  margin-top: 24px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.invite__expires.is-error {
  color: var(--color-danger);
}

.invite__role {
  margin-top: 28px;
}

.invite__label {
  margin-bottom: 12px;
  font-size: 15px;
  font-weight: 500;
}

.invite__footer {
  display: flex;
  justify-content: flex-end;
  margin-top: 34px;
}

.invite__done {
  width: 254px;
}

@media (max-width: 640px) {
  .invite__link {
    margin-top: 28px;
  }

  .invite__done {
    width: 100%;
  }
}
</style>
