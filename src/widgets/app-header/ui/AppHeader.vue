<script setup lang="ts">
import { LocaleSwitch } from '@/features/locale-switch'
import { WorkspaceSwitcher } from '@/features/workspace-switch'
import UserMenu from './UserMenu.vue'
import { isLocalWorkspace, isPreviewWorkspace } from '@/shared/api'
</script>

<template>
  <header class="header">
    <div class="header__inner">
      <WorkspaceSwitcher />
      <div class="header__right">
        <span class="header__divider" aria-hidden="true" />
        <LocaleSwitch />
        <span v-if="isLocalWorkspace" class="header__local">{{ $t('integration.localMode') }}</span>
        <span v-else-if="isPreviewWorkspace" class="header__local">{{ $t('integration.previewMode') }}</span>
        <UserMenu />
      </div>
    </div>
  </header>
</template>

<style scoped>
.header {
  background: var(--color-bg);
}

.header__inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 80px;
  max-width: var(--container-max);
  margin-inline: auto;
  padding-inline: var(--header-px);
}

.header__right {
  display: flex;
  align-items: center;
  gap: 20px;
}

.header__divider {
  width: 1px;
  height: 24px;
  background: var(--color-border-strong);
}

.header__local {
  max-width: 180px;
  color: var(--color-text-secondary);
  font-size: 12px;
  text-align: right;
}

@media (max-width: 640px) {
  .header__local {
    display: none;
  }

  .header__right {
    gap: 12px;
  }
}
</style>
