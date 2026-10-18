<script setup lang="ts">
import { LOCALES, locale } from '@/shared/i18n'

function toggle() {
  locale.value = locale.value === 'ru' ? 'en' : 'ru'
}
</script>

<template>
  <button type="button" class="locale" :aria-label="$t('header.switchTo')" :title="$t('header.switchTo')" @click="toggle">
    <span v-for="item in LOCALES" :key="item.value" class="locale__item" :class="{ 'is-active': item.value === locale }">
      {{ item.short }}
    </span>
  </button>
</template>

<style scoped>
.locale {
  display: inline-flex;
  gap: 8px;
  font-size: 12px;
  letter-spacing: 0.04em;
}

.locale__item {
  color: var(--color-text-subtle);
  transition: color var(--transition);
}

.locale__item.is-active {
  color: var(--color-text);
}

.locale:hover .locale__item:not(.is-active) {
  color: var(--color-text-secondary);
}
</style>
