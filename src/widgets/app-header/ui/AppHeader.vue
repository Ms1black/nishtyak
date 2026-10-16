<script setup lang="ts">
import { LocaleSwitch } from '@/features/locale-switch'
import { WorkspaceSwitcher } from '@/features/workspace-switch'
import UserMenu from './UserMenu.vue'
</script>

<template>
  <header class="header">
    <div class="header__inner">
      <WorkspaceSwitcher />
      <div class="header__right">
        <span class="header__divider" aria-hidden="true" />
        <LocaleSwitch />
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
</style>
