<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Dependency } from '@/entities/project'
import { BaseButton, BaseIcon, BaseInput } from '@/shared/ui'
import { splitRequirement } from '../lib/requirement'
import { dependencyRequirement, needsPackageMapping } from '../lib/dependencyDisplay'

const props = defineProps<{
  dependency: Dependency
  packageOverride?: string
  versionOverride?: string
  excluded: boolean
  disabled?: boolean
  variant?: 'editor' | 'mapping'
  unresolved?: boolean
}>()

const emit = defineEmits<{
  setPackage: [module: string, value: string]
  setVersion: [module: string, value: string]
  toggle: [module: string]
  leaveUnknown: [module: string]
}>()

const original = computed(() => splitRequirement(props.dependency.requirement ?? props.dependency.package))
const needsAnswer = computed(
  () => props.unresolved ?? needsPackageMapping(props.dependency, props.packageOverride, props.excluded),
)
const pendingPackage = ref(props.packageOverride ?? original.value.name)
const originalVersion = computed(() => {
  const requirement = dependencyRequirement(props.dependency, props.packageOverride)
  return requirement.spec.split(';')[0]!.trim()
})

function applyPackage() {
  const value = pendingPackage.value.trim()
  if (value && !props.disabled) emit('setPackage', props.dependency.module, value)
}

const packageName = computed({
  get: () => props.packageOverride ?? original.value.name,
  set: (value: string) => emit('setPackage', props.dependency.module, value),
})

const version = computed({
  get: () => props.versionOverride ?? originalVersion.value,
  set: (value: string) =>
    emit('setVersion', props.dependency.module, value === originalVersion.value ? '' : value),
})
</script>

<template>
  <form v-if="variant === 'mapping'" class="mapping" @submit.prevent="applyPackage">
    <div>
      <p class="mapping__title">
        <code>import {{ dependency.module }}</code>
      </p>
      <p class="mapping__hint">{{ $t('analysis.deps.unresolvedHint') }}</p>
    </div>
    <div class="mapping__controls">
      <BaseInput
        v-model="pendingPackage"
        mono
        :disabled="disabled"
        :placeholder="$t('analysis.deps.packagePlaceholder')"
        :aria-label="$t('analysis.deps.packageFor', { module: dependency.module })"
      />
      <BaseButton type="submit" size="sm" :disabled="disabled || !pendingPackage.trim()">
        {{ $t('analysis.deps.applyPackage') }}
      </BaseButton>
      <button
        type="button"
        class="mapping__skip"
        :disabled="disabled"
        @click="emit('leaveUnknown', dependency.module)"
      >
        {{ $t('analysis.deps.leaveUnknown') }}
      </button>
    </div>
  </form>

  <div v-else class="dep" :class="{ 'is-attention': needsAnswer, 'is-excluded': excluded }">
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
      :placeholder="
        needsAnswer ? $t('analysis.deps.packageRequired') : $t('analysis.deps.packagePlaceholder')
      "
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
.mapping {
  display: grid;
  gap: 12px;
  padding: 16px 0;
  border-bottom: 1px solid var(--color-border);
}

.mapping__title {
  font-family: var(--font-mono);
  font-size: 14px;
}

.mapping__hint {
  max-width: 680px;
  margin-top: 6px;
  color: var(--color-text-muted);
  font-size: 13px;
  line-height: 1.5;
}

.mapping__controls {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.mapping__controls > input {
  flex: 1 1 180px;
  max-width: 300px;
}

.mapping__skip {
  padding: 8px 0;
  color: var(--color-text-muted);
  font-size: 13px;
}

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
