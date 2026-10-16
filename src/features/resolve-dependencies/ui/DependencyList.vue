<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Dependency } from '@/entities/project'
import { dependencySummaries, needsPackageMapping } from '../lib/dependencyDisplay'
import DependencyRow from './DependencyRow.vue'

const props = defineProps<{
  dependencies: Dependency[]
  packages: Record<string, string>
  versions: Record<string, string>
  excludedModules: string[]
  requirements?: string[]
  unresolvedModules?: string[]
  disabled?: boolean
}>()

const emit = defineEmits<{
  setPackage: [module: string, value: string]
  setVersion: [module: string, value: string]
  toggle: [module: string]
}>()

const skippedModules = ref<string[]>([])
const installable = computed(() =>
  props.dependencies.filter((d) => d.category === 'external' || d.category === 'unresolved'),
)
const included = computed(() => installable.value.filter((d) => !props.excludedModules.includes(d.module)))
const unresolved = computed(() =>
  included.value.filter((d) =>
    needsPackageMapping(d, props.packages[d.module], false, props.unresolvedModules),
  ),
)
const questions = computed(() => unresolved.value.filter((d) => !skippedModules.value.includes(d.module)))
const skipped = computed(() => unresolved.value.filter((d) => skippedModules.value.includes(d.module)))
const summarized = computed(() =>
  dependencySummaries(
    included.value,
    props.packages,
    props.versions,
    props.requirements,
    props.unresolvedModules,
  ),
)
const excludedCount = computed(() => installable.value.length - included.value.length)

function leaveUnknown(module: string) {
  if (!skippedModules.value.includes(module)) skippedModules.value.push(module)
}

function clarify(module: string) {
  skippedModules.value = skippedModules.value.filter((item) => item !== module)
}

const local = computed(() => props.dependencies.filter((d) => d.category === 'local').map((d) => d.module))
const stdlib = computed(() => props.dependencies.filter((d) => d.category === 'stdlib').map((d) => d.module))
</script>

<template>
  <div class="deps">
    <div v-if="questions.length" class="deps__questions">
      <p class="deps__question-title">{{ $t('analysis.deps.unresolvedTitle') }}</p>
      <DependencyRow
        v-for="dep in questions"
        :key="dep.id"
        variant="mapping"
        :dependency="dep"
        :package-override="packages[dep.module]"
        :excluded="false"
        :disabled="disabled"
        @set-package="(m, v) => emit('setPackage', m, v)"
        @leave-unknown="leaveUnknown"
      />
    </div>

    <ul v-if="summarized.length || skipped.length" class="deps__summary">
      <li v-for="dep in summarized" :key="dep.id" class="deps__summary-row">
        <code>{{ dep.name }}</code>
        <span class="deps__version">
          {{ dep.spec || $t('analysis.deps.unknownVersion') }}
        </span>
      </li>
      <li v-for="dep in skipped" :key="`unknown-${dep.id}`" class="deps__summary-row">
        <code>import {{ dep.module }}</code>
        <span class="deps__version">{{ $t('analysis.deps.unknownPackage') }}</span>
        <button type="button" class="deps__clarify" :disabled="disabled" @click="clarify(dep.module)">
          {{ $t('analysis.deps.clarify') }}
        </button>
      </li>
    </ul>

    <p v-if="!summarized.length && !unresolved.length && !excludedCount" class="deps__empty">
      {{ $t('analysis.deps.empty') }}
    </p>
    <p v-if="excludedCount" class="deps__excluded">
      {{ $t('analysis.deps.excludedSummary', { n: excludedCount }) }}
    </p>

    <details v-if="local.length || stdlib.length" class="deps__automatic">
      <summary>
        {{ $t('analysis.deps.automaticSummary', { local: local.length, stdlib: stdlib.length }) }}
      </summary>
      <p class="deps__builtin">
        {{ $t('analysis.deps.builtin') }}
        <template v-if="local.length">
          <code v-for="m in local" :key="m">{{ m }}</code> {{ $t('analysis.deps.local')
          }}<template v-if="stdlib.length">; </template>
        </template>
        <template v-if="stdlib.length">
          <code v-for="m in stdlib" :key="m">{{ m }}</code> {{ $t('analysis.deps.stdlib') }}
        </template>
      </p>
    </details>

    <details v-if="installable.length" class="deps__advanced">
      <summary>{{ $t('analysis.deps.editOptional') }}</summary>
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
        :unresolved="unresolved.some((item) => item.module === dep.module)"
        :disabled="disabled"
        @set-package="(m, v) => emit('setPackage', m, v)"
        @set-version="(m, v) => emit('setVersion', m, v)"
        @toggle="emit('toggle', $event)"
      />
    </details>
  </div>
</template>

<style scoped>
.deps__question-title {
  font-size: 14px;
  font-weight: 500;
}

.deps__summary {
  margin: 0;
  padding: 0;
  list-style: none;
}

.deps__summary-row {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 8px 16px;
  padding: 10px 0;
  border-bottom: 1px solid var(--color-border);
  font-size: 14px;
}

.deps__summary-row code {
  overflow-wrap: anywhere;
  font-family: var(--font-mono);
}

.deps__version,
.deps__excluded {
  color: var(--color-text-muted);
  font-size: 13px;
  overflow-wrap: anywhere;
}

.deps__clarify {
  margin-left: auto;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.deps__advanced,
.deps__automatic {
  margin-top: 16px;
  color: var(--color-text-muted);
  font-size: 13px;
}

.deps__advanced > summary,
.deps__automatic > summary {
  cursor: pointer;
  line-height: 1.6;
}

.deps__advanced[open] > summary {
  margin-bottom: 16px;
}

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
