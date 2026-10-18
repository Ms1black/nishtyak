<script setup lang="ts">
withDefaults(
  defineProps<{
    tag?: string
    padding?: 'md' | 'lg'
  }>(),
  { tag: 'section', padding: 'md' },
)
</script>

<template>
  <component :is="tag" class="card" :class="`card--${padding}`">
    <slot />
  </component>
</template>

<style scoped>
.card {
  background: var(--color-surface);
  border-radius: var(--radius-xl);
}

.card--md {
  padding: 28px;
}

.card--lg {
  padding: 32px;
}

@media (max-width: 640px) {
  .card--md,
  .card--lg {
    padding: 20px;
  }
}
</style>
