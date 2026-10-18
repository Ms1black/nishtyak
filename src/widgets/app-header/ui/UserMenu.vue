<script setup lang="ts">
import { ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { UserAvatar, useUserStore } from '@/entities/user'
import { useLogout } from '@/features/auth-logout'
import { RouteName } from '@/shared/config'
import { useClickOutside } from '@/shared/lib'
import { BaseIcon } from '@/shared/ui'
import { isLocalWorkspace, isPreviewWorkspace } from '@/shared/api'

const userStore = useUserStore()
const route = useRoute()
const { logout, isLoggingOut } = useLogout()

const root = ref<HTMLElement | null>(null)
const isOpen = ref(false)

useClickOutside(root, () => (isOpen.value = false))
</script>

<template>
  <div ref="root" class="user-menu" @keydown.esc="isOpen = false">
    <button
      type="button"
      class="user-menu__trigger"
      :class="{ 'is-active': isOpen || route.name === RouteName.Profile }"
      aria-haspopup="menu"
      :aria-expanded="isOpen"
      :aria-label="$t('header.menu')"
      @click="isOpen = !isOpen"
    >
      <UserAvatar :name="userStore.currentUser.name" :src="userStore.currentUser.avatarUrl" />
    </button>

    <Transition name="pop">
      <div v-if="isOpen" class="user-menu__panel" role="menu">
        <div class="user-menu__who">
          <UserAvatar :name="userStore.currentUser.name" :src="userStore.currentUser.avatarUrl" :size="40" />
          <div class="user-menu__who-text">
            <span class="user-menu__name">{{ userStore.currentUser.name }}</span>
            <span class="user-menu__email">{{ userStore.currentUser.email }}</span>
          </div>
        </div>
        <RouterLink :to="{ name: RouteName.Profile }" class="user-menu__item" role="menuitem" @click="isOpen = false">
          <BaseIcon name="user" :size="18" />
          {{ $t('routes.profile') }}
        </RouterLink>
        <RouterLink v-if="isPreviewWorkspace" :to="{ name: RouteName.Login }" class="user-menu__item" role="menuitem" @click="isOpen = false">
          <BaseIcon name="user" :size="18" />
          {{ $t('integration.loginScreen') }}
        </RouterLink>
        <button type="button" class="user-menu__item" role="menuitem" :disabled="isLoggingOut" @click="logout">
          <BaseIcon name="logout" :size="18" />
          {{ $t(isLocalWorkspace ? 'integration.loginScreen' : 'header.logout') }}
        </button>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.user-menu {
  position: relative;
}

.user-menu__trigger {
  display: block;
  border-radius: 50%;
}

.user-menu__trigger :deep(.avatar) {
  transition: border-color var(--transition);
}

.user-menu__trigger:hover :deep(.avatar),
.user-menu__trigger.is-active :deep(.avatar) {
  border-color: var(--color-text);
}

.user-menu__panel {
  position: absolute;
  z-index: 30;
  top: calc(100% + 10px);
  right: 0;
  width: 260px;
  padding: 6px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-raised);
  box-shadow: 0 16px 40px rgb(0 0 0 / 0.5);
}

.user-menu__who {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 10px 14px;
  margin-bottom: 6px;
  border-bottom: 1px solid var(--color-border-strong);
}

.user-menu__who-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.user-menu__name {
  overflow: hidden;
  font-size: 14px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-menu__email {
  overflow: hidden;
  color: var(--color-text-muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-menu__item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 10px;
  border-radius: var(--radius-sm);
  color: var(--color-text-secondary);
  font-size: 14px;
  text-align: left;
  transition:
    background-color var(--transition),
    color var(--transition);
}

.user-menu__item:hover:not(:disabled) {
  background: #303030;
  color: var(--color-text);
}

.pop-enter-active,
.pop-leave-active {
  transition:
    opacity var(--transition),
    transform var(--transition);
}

.pop-enter-from,
.pop-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
