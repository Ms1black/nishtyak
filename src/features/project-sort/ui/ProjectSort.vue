<script setup lang="ts">
import { computed } from 'vue'
import { t } from '@/shared/i18n'
import { BaseIcon } from '@/shared/ui'
import type { DateSortOrder } from '../lib/sortByDate'

const order = defineModel<DateSortOrder>({ required: true })

const label = computed(() => (order.value === 'newest' ? t('projects.sortNewest') : t('projects.sortOldest')))

function toggle() {
  order.value = order.value === 'newest' ? 'oldest' : 'newest'
}
</script>

<template>
  <button type="button" class="sort" :aria-label="$t('projects.sortLabel', { order: label })" @click="toggle">
    {{ label }}
    <BaseIcon name="sort" :size="20" class="sort__icon" :class="{ 'is-flipped': order === 'oldest' }" />
  </button>
</template>

<style scoped>
.sort {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  height: 40px;
  color: var(--color-text-secondary);
  font-size: 15px;
  transition: color var(--transition);
}

.sort:hover {
  color: var(--color-text);
}

.sort__icon {
  transition: transform var(--transition);
}

.sort__icon.is-flipped {
  transform: scaleY(-1);
}
</style>
