<script setup lang="ts">
import { computed } from 'vue'
import type { Dependency } from '@/entities/project'
import { BaseIcon, BaseInput } from '@/shared/ui'
import { splitRequirement } from '../lib/requirement'

const props = defineProps<{
  dependency: Dependency
  packageOverride?: string
  versionOverride?: string
  excluded: boolean
  disabled?: boolean
}>()

const emit = defineEmits<{
  setPackage: [module: string, value: string]
  setVersion: [module: string, value: string]
  toggle: [module: string]
}>()

const original = computed(() => splitRequirement(props.dependency.requirement ?? props.dependency.package))
const needsAnswer = computed(() => !props.excluded && !props.packageOverride && !original.value.name)

const packageName = computed({
  get: () => props.packageOverride ?? original.value.name,
  set: (value: string) => emit('setPackage', props.dependency.module, value === original.value.name ? '' : value),
})

const version = computed({
  get: () => props.versionOverride ?? original.value.spec,
  set: (value: string) => emit('setVersion', props.dependency.module, value === original.value.spec ? '' : value),
})
</script>

<template>
  <div class="dep" :class="{ 'is-attention': needsAnswer, 'is-excluded': excluded }">
    <div class="dep__import">
      <span class="dep__module">
        <BaseIcon v-if="needsAnswer" name="alert" :size="16" class="dep__alert" />
        import {{ dependency.module }}
      </span>
    </div>

    <BaseIcon name="arrow-right" :size="16" class="dep__arrow" />

    <BaseInput
      v-model="packageName"
      mono
      :attention="needsAnswer"
      :disabled="excluded || disabled"
      :placeholder="needsAnswer ? $t('analysis.deps.packageRequired') : $t('analysis.deps.packagePlaceholder')"
      :aria-label="$t('analysis.deps.packageFor', { module: dependency.module })"
    />
    <BaseInput
      v-model="version"
      mono
      :disabled="excluded || disabled"
      :placeholder="$t('analysis.deps.versionPlaceholder')"
      :aria-label="$t('analysis.deps.versionFor', { module: dependency.module })"
    />

    <button
      type="button"
      class="dep__toggle"
      :disabled="disabled"
      :title="excluded ? $t('analysis.deps.includeTitle') : $t('analysis.deps.excludeTitle')"
      @click="emit('toggle', dependency.module)"
    >
      {{ excluded ? $t('analysis.deps.include') : $t('analysis.deps.exclude') }}
    </button>
  </div>
</template>

<style scoped>
.dep {
  display: grid;
  grid-template-columns: minmax(180px, 1.1fr) 16px minmax(140px, 1fr) minmax(110px, 0.6fr) 96px;
  align-items: center;
  gap: 12px;
  padding: 14px 0;
  border-bottom: 1px solid var(--color-border);
}

.dep:last-child {
  border-bottom: 0;
}

.dep__import {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.dep__module {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  overflow: hidden;
  font-family: var(--font-mono);
  font-size: 14px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dep__alert {
  color: var(--color-text);
}

.dep__arrow {
  color: var(--color-text-subtle);
}

.dep__toggle {
  justify-self: end;
  color: var(--color-text-muted);
  font-size: 13px;
  transition: color var(--transition);
}

.dep__toggle:hover:not(:disabled) {
  color: var(--color-text);
}

.dep.is-excluded .dep__import {
  opacity: 0.45;
}

.dep.is-excluded .dep__module {
  text-decoration: line-through;
}

@media (max-width: 760px) {
  .dep {
    grid-template-columns: 1fr 1fr;
  }

  .dep__import {
    grid-column: 1 / -1;
  }

  .dep__arrow {
    display: none;
  }

  .dep__toggle {
    grid-column: 1 / -1;
  }
}
</style>
