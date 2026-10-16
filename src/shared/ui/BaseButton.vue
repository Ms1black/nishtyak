<script setup lang="ts">
import BaseIcon from './BaseIcon.vue'
import type { IconName } from './icons'

withDefaults(
  defineProps<{
    variant?: 'primary' | 'secondary' | 'tonal' | 'danger'
    size?: 'sm' | 'md' | 'lg'
    type?: 'button' | 'submit'
    icon?: IconName
    iconEnd?: IconName
    disabled?: boolean
    loading?: boolean
    block?: boolean
  }>(),
  { variant: 'primary', size: 'md', type: 'button' },
)
</script>

<template>
  <button
    :type="type"
    class="btn"
    :class="[`btn--${variant}`, `btn--${size}`, { 'btn--block': block }]"
    :disabled="disabled || loading"
    :aria-busy="loading || undefined"
  >
    <BaseIcon v-if="loading" name="refresh" :size="18" spin />
    <BaseIcon v-else-if="icon" :name="icon" :size="18" />
    <span><slot /></span>
    <BaseIcon v-if="iconEnd && !loading" :name="iconEnd" :size="18" />
  </button>
</template>

<style scoped>
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  border-radius: var(--radius-pill);
  font-weight: 500;
  white-space: nowrap;
  transition:
    background-color var(--transition),
    color var(--transition),
    opacity var(--transition);
}

.btn--sm {
  height: 36px;
  padding: 0 20px;
  font-size: 14px;
}

.btn--md {
  height: 46px;
  padding: 0 28px;
  font-size: 15px;
}

.btn--lg {
  height: 48px;
  padding: 0 40px;
  font-size: 15px;
}

.btn--block {
  width: 100%;
}

.btn--primary {
  background: var(--color-inverse-bg);
  color: var(--color-inverse-text);
}

.btn--primary:hover:not(:disabled) {
  background: #e6e6e6;
}

.btn--secondary {
  background: var(--color-bg);
  color: var(--color-text);
}

.btn--secondary:hover:not(:disabled) {
  background: #0d0d0d;
}

.btn--tonal {
  background: var(--color-surface-raised);
  color: var(--color-text);
}

.btn--tonal:hover:not(:disabled) {
  background: #303030;
}

.btn--danger {
  background: rgb(255 77 77 / 0.14);
  color: #ff5c5c;
}

.btn--danger:hover:not(:disabled) {
  background: rgb(255 77 77 / 0.22);
}

.btn:disabled {
  background: var(--color-surface-raised);
  color: #7a7a7a;
}

.btn[aria-busy='true'] {
  color: var(--color-text-secondary);
}
</style>
