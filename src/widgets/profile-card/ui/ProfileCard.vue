<script setup lang="ts">
import { UserAvatar, useUserStore } from '@/entities/user'
import { useLogout } from '@/features/auth-logout'
import { ProfileNameForm } from '@/features/profile-edit'
import { BaseButton, BaseCard } from '@/shared/ui'
import { isLocalWorkspace } from '@/shared/api'

const userStore = useUserStore()
const { logout, isLoggingOut } = useLogout()
</script>

<template>
  <BaseCard tag="section" padding="lg" class="profile" :aria-label="$t('profile.label')">
    <div class="profile__head">
      <UserAvatar :name="userStore.currentUser.name" :src="userStore.currentUser.avatarUrl" :size="72" />
      <div class="profile__text">
        <h1 class="profile__name">{{ userStore.currentUser.name }}</h1>
        <p class="profile__email">{{ userStore.currentUser.email || '…' }}</p>
      </div>
    </div>
    <ProfileNameForm class="profile__form" />
    <BaseButton variant="tonal" icon="logout" block class="profile__logout" :loading="isLoggingOut" @click="logout">
      {{ $t(isLocalWorkspace ? 'integration.loginScreen' : 'header.logout') }}
    </BaseButton>
  </BaseCard>
</template>

<style scoped>
.profile__head {
  display: flex;
  align-items: center;
  gap: 18px;
}

.profile__text {
  min-width: 0;
}

.profile__name {
  overflow: hidden;
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 500;
  line-height: 1.25;
  text-overflow: ellipsis;
}

.profile__email {
  margin-top: 4px;
  overflow: hidden;
  color: var(--color-text-muted);
  font-size: 14px;
  text-overflow: ellipsis;
}

.profile__form {
  margin-top: 32px;
}

.profile__logout {
  margin-top: 20px;
}

</style>
