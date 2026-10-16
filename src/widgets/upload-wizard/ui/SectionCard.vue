<script setup lang="ts">
import { BaseCard } from '@/shared/ui'

defineProps<{
  id: string
  title: string
  subtitle?: string
  questions?: number
  errors?: number
}>()
</script>

<template>
  <BaseCard :id="id" padding="lg" class="section" tabindex="-1">
    <header class="section__head">
      <div>
        <h2 class="section__title">{{ title }}</h2>
        <p v-if="subtitle" class="section__subtitle">{{ subtitle }}</p>
      </div>
      <span v-if="errors" class="section__badge section__badge--error">
        {{ $t('units.errors', errors) }}
      </span>
      <span v-else-if="questions" class="section__badge">
        {{ $t('units.questions', questions) }}
      </span>
    </header>
    <slot />
  </BaseCard>
</template>

<style scoped>
.section {
  scroll-margin-top: 24px;
  transition: box-shadow 400ms ease;
}

.section:focus {
  outline: none;
}

.section.is-highlighted {
  box-shadow: 0 0 0 1px var(--color-text-muted);
}

.section__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 24px;
}

.section__title {
  font-family: var(--font-display);
  font-size: 20px;
  font-weight: 500;
  line-height: 1.25;
}

.section__subtitle {
  margin-top: 4px;
  color: var(--color-text-muted);
  font-size: 13px;
}

.section__badge {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
  height: 26px;
  padding: 0 12px;
  border: 1px dashed var(--color-text-muted);
  border-radius: var(--radius-pill);
  font-size: 12px;
  white-space: nowrap;
}

.section__badge--error {
  border: 0;
  background: rgb(255 77 77 / 0.12);
  color: #ff5c5c;
}
</style>
