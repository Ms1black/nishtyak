<script setup lang="ts">
import { BaseCard, BaseIcon } from '@/shared/ui'

defineProps<{
  id: string
  title: string
  subtitle?: string
  errors?: number
  collapsible?: boolean
}>()

const open = defineModel<boolean>('open', { default: false })

function onToggle(event: Event) {
  if (event.target === event.currentTarget) open.value = (event.target as HTMLDetailsElement).open
}
</script>

<template>
  <BaseCard
    :id="id"
    :tag="collapsible ? 'details' : 'section'"
    :open="collapsible ? open : undefined"
    padding="lg"
    class="section"
    tabindex="-1"
    @toggle="onToggle"
  >
    <component
      :is="collapsible ? 'summary' : 'header'"
      class="section__head"
      :class="{ section__toggle: collapsible }"
    >
      <div>
        <h2 class="section__title">{{ title }}</h2>
        <p v-if="subtitle" class="section__subtitle">{{ subtitle }}</p>
      </div>
      <span v-if="errors" class="section__badge section__badge--error">
        {{ $t('units.errors', errors) }}
      </span>
      <BaseIcon v-if="collapsible" name="chevron-down" :size="20" class="section__chevron" />
    </component>
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

.section__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 24px;
}

.section__toggle {
  cursor: pointer;
  list-style: none;
  margin-bottom: 0;
}

.section__toggle::-webkit-details-marker {
  display: none;
}

.section[open] > .section__toggle {
  margin-bottom: 24px;
}

.section__chevron {
  flex-shrink: 0;
  margin-left: auto;
  color: var(--color-text-muted);
}

.section[open] > .section__toggle .section__chevron {
  transform: rotate(180deg);
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
