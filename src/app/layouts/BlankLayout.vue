<script setup lang="ts">
import { LocaleSwitch } from '@/features/locale-switch'
import { APP_NAME } from '@/shared/config'
import { BrandMark } from '@/shared/ui'
import { AppFooter } from '@/widgets/app-footer'
</script>

<template>
  <div class="blank">
    <header class="blank__header">
      <span class="blank__brand"><BrandMark :size="22" />{{ APP_NAME }}</span>
      <LocaleSwitch />
    </header>
    <main class="blank__main">
      <slot />
    </main>
    <div class="container">
      <AppFooter />
    </div>
  </div>
</template>

<style scoped>
.blank {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}

.blank__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  max-width: var(--container-max);
  height: 80px;
  margin-inline: auto;
  padding-inline: var(--header-px);
}

.blank__brand {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  font-family: var(--font-display);
  font-size: 20px;
  font-weight: 600;
  letter-spacing: -0.01em;
}

.blank__main {
  display: flex;
  flex: 1;
  flex-direction: column;
  padding-block: 48px;
}
</style>
