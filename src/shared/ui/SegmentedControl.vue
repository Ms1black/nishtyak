<script setup lang="ts" generic="T extends string | number">
import { RouterLink } from 'vue-router'
import type { SegmentedItem } from './types'

withDefaults(
  defineProps<{
    items: SegmentedItem<T>[]
    modelValue: T
    stretch?: boolean
    size?: 'md' | 'sm'
    tone?: 'surface' | 'dark'
    ariaLabel?: string
  }>(),
  { stretch: false, size: 'md', tone: 'surface' },
)

const emit = defineEmits<{ 'update:modelValue': [value: T] }>()
</script>

<template>
  <div
    class="segmented"
    :class="[`segmented--${size}`, `segmented--${tone}`, { 'segmented--stretch': stretch }]"
    role="tablist" :aria-label="ariaLabel">
    <template v-for="item in items" :key="item.value">
      <RouterLink
        v-if="item.to"
        :to="item.to"
        class="segmented__item"
        :class="{ 'is-active': item.value === modelValue }"
        role="tab"
        :aria-selected="item.value === modelValue"
      >
        <span v-if="item.prefix" class="segmented__prefix">{{ item.prefix }}</span>
        <span class="segmented__label">{{ item.label }}</span>
      </RouterLink>
      <button
        v-else
        type="button"
        class="segmented__item"
        :class="{ 'is-active': item.value === modelValue }"
        role="tab"
        :aria-selected="item.value === modelValue"
        :disabled="item.disabled"
        @click="emit('update:modelValue', item.value)"
      >
        <span v-if="item.prefix" class="segmented__prefix">{{ item.prefix }}</span>
        <span class="segmented__label">{{ item.label }}</span>
      </button>
    </template>
  </div>
</template>

<style scoped>
.segmented {
  display: inline-flex;
  gap: 4px;
  padding: 6px;
  border-radius: var(--radius-pill);
  background: var(--color-surface);
  max-width: 100%;
  overflow-x: auto;
  scrollbar-width: none;
}

.segmented--stretch {
  display: flex;
  width: 100%;
}

.segmented--stretch .segmented__item {
  flex: 1 1 0;
}

.segmented__item {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 40px;
  padding: 0 24px;
  border-radius: var(--radius-pill);
  color: var(--color-text-muted);
  font-size: 15px;
  white-space: nowrap;
  transition:
    background-color var(--transition),
    color var(--transition);
}

.segmented__item:not(.is-active):not(:disabled):hover {
  color: var(--color-text);
}

.segmented__item:disabled {
  cursor: default;
}

.segmented__item.is-active {
  background: var(--color-inverse-bg);
  color: var(--color-inverse-text);
}

.segmented--dark {
  background: var(--color-bg);
}

.segmented--sm {
  padding: 4px;
}

.segmented--sm .segmented__item {
  height: 34px;
  padding: 0 16px;
  font-size: 14px;
}

.segmented__prefix {
  font-variant-numeric: tabular-nums;
}
</style>
