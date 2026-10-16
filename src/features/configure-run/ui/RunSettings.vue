<script setup lang="ts">
import { computed } from 'vue'
import { formatRunCommand, type RunKind, type RunSpec } from '@/entities/project'
import { t } from '@/shared/i18n'
import { BaseInput, BaseSelect, SegmentedControl, type SegmentedItem, type SelectOption } from '@/shared/ui'

const props = defineProps<{
  entrypoints: string[]
  scriptFiles: string[]
  pythonRequires: string | null
  disabled?: boolean
}>()

const runSpec = defineModel<RunSpec | null>('runSpec', { required: true })
const pythonVersion = defineModel<string | null>('pythonVersion', { required: true })

const kinds = computed<SegmentedItem<RunKind>[]>(() =>
  (['script', 'module', 'callable'] as const).map((value) => ({ value, label: t(`analysis.run.kinds.${value}`) })),
)

const PYTHON_VERSIONS: SelectOption[] = ['3.9', '3.10', '3.11', '3.12', '3.13'].map((v) => ({
  value: v,
  label: `Python ${v}`,
}))

const kind = computed(() => runSpec.value?.kind ?? 'script')

const scriptOptions = computed<SelectOption[]>(() => {
  const entry = new Set(props.entrypoints)
  return [...props.entrypoints, ...props.scriptFiles.filter((f) => !entry.has(f))].map((path) => ({
    value: path,
    label: path,
    description: entry.has(path) ? t('analysis.run.entrypoint') : undefined,
    icon: 'file-code',
  }))
})

function patch(changes: Partial<RunSpec>) {
  const base: RunSpec = runSpec.value ?? { kind: 'script', target: '', args: [], workingDirectory: '.' }
  runSpec.value = { ...base, ...changes }
}

function setKind(next: RunKind) {
  if (next !== kind.value) patch({ kind: next, target: '' })
}

const target = computed({
  get: () => runSpec.value?.target ?? '',
  set: (value: string | null) => patch({ target: value ?? '' }),
})

const args = computed({
  get: () => runSpec.value?.args.join(' ') ?? '',
  set: (value: string) => patch({ args: value.split(/\s+/).filter(Boolean) }),
})

const workingDirectory = computed({
  get: () => runSpec.value?.workingDirectory ?? '.',
  set: (value: string) => patch({ workingDirectory: value || '.' }),
})

const command = computed(() => formatRunCommand(runSpec.value))
</script>

<template>
  <div class="run">
    <div class="run__field">
      <span class="run__label">{{ $t('analysis.run.what') }}</span>
      <SegmentedControl
        :items="kinds"
        :model-value="kind"
        size="sm"
        tone="dark"
        :aria-label="$t('analysis.run.kindLabel')"
        @update:model-value="setKind"
      />
    </div>

    <div class="run__field">
      <label class="run__label" for="run-target">
        {{ $t(`analysis.run.target.${kind}`) }}
      </label>
      <BaseSelect
        v-if="kind === 'script'"
        v-model="target"
        :options="scriptOptions"
        :placeholder="$t('analysis.run.chooseFile')"
        icon="file-code"
        :disabled="disabled"
      />
      <BaseInput
        v-else
        id="run-target"
        v-model="target"
        mono
        :attention="!target"
        :disabled="disabled"
        :placeholder="kind === 'module' ? 'sales_report.cli' : 'sales_report.cli:main'"
      />
    </div>

    <div class="run__row">
      <div class="run__field">
        <label class="run__label" for="run-args">{{ $t('analysis.run.args') }}</label>
        <BaseInput id="run-args" v-model="args" mono placeholder="--month 10" :disabled="disabled" />
      </div>
      <div class="run__field">
        <label class="run__label" for="run-cwd">{{ $t('analysis.run.cwd') }}</label>
        <BaseInput id="run-cwd" v-model="workingDirectory" mono placeholder="." :disabled="disabled" />
      </div>
    </div>

    <div class="run__field">
      <span id="python-label" class="run__label">
        {{ $t('analysis.run.python') }}
        <span v-if="pythonRequires" class="run__hint">{{ $t('analysis.run.pythonDeclared', { spec: pythonRequires }) }}</span>
      </span>
      <BaseSelect
        v-model="pythonVersion"
        :options="PYTHON_VERSIONS"
        :placeholder="$t('analysis.run.pythonPlaceholder')"
        aria-labelledby="python-label"
        :disabled="disabled"
      />
    </div>

    <div class="run__preview">
      <span class="run__preview-label">{{ $t('analysis.run.command') }}</span>
      <code><span aria-hidden="true">$ </span>{{ command }}</code>
    </div>
  </div>
</template>

<style scoped>
.run {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.run__field {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
}

.run__field > .segmented {
  align-self: flex-start;
}

.run__label {
  font-size: 14px;
  font-weight: 500;
}

.run__hint {
  margin-left: 8px;
  color: var(--color-text-muted);
  font-weight: 400;
}

.run__row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.run__preview {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px 16px;
  border-radius: var(--radius-md);
  background: var(--color-bg);
}

.run__preview-label {
  color: var(--color-text-subtle);
  font-size: 12px;
}

.run__preview code {
  overflow-x: auto;
  font-family: var(--font-mono);
  font-size: 13px;
  white-space: nowrap;
}

.run__preview code span {
  color: var(--color-text-subtle);
}

@media (max-width: 640px) {
  .run__row {
    grid-template-columns: 1fr;
  }
}
</style>
