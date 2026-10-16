<script setup lang="ts">
import { computed } from 'vue'
import type { Dependency } from '@/entities/project'
import DependencyRow from './DependencyRow.vue'

const props = defineProps<{
  dependencies: Dependency[]
  packages: Record<string, string>
  versions: Record<string, string>
  excludedModules: string[]
  disabled?: boolean
}>()

const emit = defineEmits<{
  setPackage: [module: string, value: string]
  setVersion: [module: string, value: string]
  toggle: [module: string]
}>()

const installable = computed(() =>
  props.dependencies
    .filter((d) => d.category === 'external' || d.category === 'unresolved')
    .sort((a, b) => Number(b.category === 'unresolved') - Number(a.category === 'unresolved')),
)

const local = computed(() => props.dependencies.filter((d) => d.category === 'local').map((d) => d.module))
const stdlib = computed(() => props.dependencies.filter((d) => d.category === 'stdlib').map((d) => d.module))
</script>

<template>
  <div class="deps">
    <div class="deps__head" aria-hidden="true">
      <span>{{ $t('analysis.deps.columnImport') }}</span>
      <span />
      <span>{{ $t('analysis.deps.columnPackage') }}</span>
      <span>{{ $t('analysis.deps.columnVersion') }}</span>
      <span />
    </div>

    <DependencyRow
      v-for="dep in installable"
      :key="dep.id"
      :dependency="dep"
      :package-override="packages[dep.module]"
      :version-override="versions[dep.module]"
      :excluded="excludedModules.includes(dep.module)"
      :disabled="disabled"
      @set-package="(m, v) => emit('setPackage', m, v)"
      @set-version="(m, v) => emit('setVersion', m, v)"
      @toggle="emit('toggle', $event)"
    />

    <p v-if="!installable.length" class="deps__empty">{{ $t('analysis.deps.empty') }}</p>

    <p v-if="local.length || stdlib.length" class="deps__builtin">
      {{ $t('analysis.deps.builtin') }}
      <template v-if="local.length">
        <code v-for="m in local" :key="m">{{ m }}</code> {{ $t('analysis.deps.local') }}<template v-if="stdlib.length">; </template>
      </template>
      <template v-if="stdlib.length">
        <code v-for="m in stdlib" :key="m">{{ m }}</code> {{ $t('analysis.deps.stdlib') }}
      </template>
    </p>
  </div>
</template>

<style scoped>
.deps__head {
  display: grid;
  grid-template-columns: minmax(180px, 1.1fr) 16px minmax(140px, 1fr) minmax(110px, 0.6fr) 96px;
  gap: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--color-border);
  color: var(--color-text-muted);
  font-size: 12px;
}

.deps__empty {
  padding: 24px 0;
  color: var(--color-text-muted);
  font-size: 14px;
}

.deps__builtin {
  margin-top: 16px;
  color: var(--color-text-muted);
  font-size: 13px;
  line-height: 1.8;
}

.deps__builtin code {
  margin: 0 4px 0 0;
  padding: 2px 8px;
  border-radius: var(--radius-xs);
  background: var(--color-bg);
  color: var(--color-text-secondary);
  font-family: var(--font-mono);
  font-size: 12px;
}

@media (max-width: 760px) {
  .deps__head {
    display: none;
  }
}
</style>
