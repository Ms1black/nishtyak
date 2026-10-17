<script setup lang="ts">
import { computed } from 'vue'
import { formatRunCommand, type LaunchCandidate, type RunKind, type RunSpec } from '@/entities/project'
import { t } from '@/shared/i18n'
import {
  BaseInput,
  BaseSelect,
  BaseTextarea,
  CodeBlock,
  InlineCode,
  SegmentedControl,
  type SegmentedItem,
  type SelectOption,
} from '@/shared/ui'

const props = defineProps<{
  entrypoints: string[]
  scriptFiles: string[]
  pythonRequires: string | null
  candidates?: LaunchCandidate[]
  hasRunOverride?: boolean
  disabled?: boolean
}>()

const runSpec = defineModel<RunSpec | null>('runSpec', { required: true })
const pythonVersion = defineModel<string | null>('pythonVersion', { required: true })

const kinds = computed<SegmentedItem<RunKind>[]>(() =>
  (['script', 'module', 'callable'] as const).map((value) => ({
    value,
    label: t(`analysis.run.kinds.${value}`),
    disabled: props.disabled,
  })),
)

const PYTHON_VERSIONS: SelectOption[] = ['3.8', '3.9', '3.10', '3.11', '3.12', '3.13', '3.14'].map((v) => ({
  value: v,
  label: `Python ${v}`,
}))

const kind = computed(() => runSpec.value?.kind ?? 'script')

const scriptOptions = computed<SelectOption[]>(() => {
  const entry = new Set(props.entrypoints.filter((path) => props.scriptFiles.includes(path)))
  return [...entry, ...props.scriptFiles.filter((f) => !entry.has(f))].map((path) => ({
    value: path,
    label: path,
    description: entry.has(path) ? t('analysis.run.entrypoint') : undefined,
    icon: 'file-code',
  }))
})

function patch(changes: Partial<RunSpec>) {
  if (props.disabled) return
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
  get: () => runSpec.value?.args.join('\n') ?? '',
  set: (value: string) => patch({ args: value.split('\n').filter((argument) => argument !== '') }),
})

const workingDirectory = computed({
  get: () => runSpec.value?.workingDirectory ?? '.',
  set: (value: string) => patch({ workingDirectory: value || '.' }),
})

const command = computed(() => formatRunCommand(runSpec.value))
</script>

<template>
  <div class="run">
    <div v-if="hasRunOverride" class="run__field">
      <button type="button" class="run__reset" :disabled="disabled" @click="runSpec = null">
        {{ $t('analysis.run.useAutomatic') }}
      </button>
      <p class="run__hint">{{ $t('analysis.run.automaticHint') }}</p>
    </div>
    <div v-if="candidates?.length" class="run__field">
      <span class="run__label">{{ $t('workflow.launchCandidates') }}</span>
      <button
        v-for="candidate in candidates"
        :key="candidate.id"
        type="button"
        class="run__candidate"
        :disabled="disabled"
        @click="runSpec = { ...candidate.runSpec, args: [...candidate.runSpec.args] }"
      >
        <InlineCode :code="formatRunCommand(candidate.runSpec)" language="bash" />
        <span>{{ candidate.reason }}</span>
      </button>
      <p class="run__hint">{{ $t('workflow.launchHint') }}</p>
    </div>
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
        <BaseTextarea
          id="run-args"
          v-model="args"
          :rows="2"
          :placeholder="'--input\nmy data.csv'"
          :disabled="disabled"
        />
        <p class="run__hint">{{ $t('workflow.argumentsHint') }}</p>
      </div>
      <div class="run__field">
        <label class="run__label" for="run-cwd">{{ $t('analysis.run.cwd') }}</label>
        <BaseInput id="run-cwd" v-model="workingDirectory" mono placeholder="." :disabled="disabled" />
      </div>
    </div>

    <div class="run__field">
      <span id="python-label" class="run__label">
        {{ $t('analysis.run.python') }}
        <span v-if="pythonRequires" class="run__hint">{{
          $t('analysis.run.pythonDeclared', { spec: pythonRequires })
        }}</span>
      </span>
      <BaseSelect
        v-model="pythonVersion"
        :options="PYTHON_VERSIONS"
        :placeholder="$t('analysis.run.pythonPlaceholder')"
        aria-labelledby="python-label"
        :disabled="disabled"
      />
      <button
        v-if="pythonVersion"
        type="button"
        class="run__reset"
        :disabled="disabled"
        @click="pythonVersion = null"
      >
        {{ $t('analysis.run.pythonUnknown') }}
      </button>
    </div>

    <div class="run__preview">
      <span class="run__preview-label">{{ $t('analysis.run.command') }}</span>
      <CodeBlock :code="command" language="bash" :line-numbers="false" />
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

.run__reset {
  align-self: flex-start;
  color: var(--color-text-secondary);
  font-size: 13px;
  text-align: left;
}

.run__reset:hover:not(:disabled) {
  color: var(--color-text);
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

.run__candidate {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  text-align: left;
}

.run__candidate span {
  color: var(--color-text-muted);
  font-size: 12px;
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

@media (max-width: 640px) {
  .run__row {
    grid-template-columns: 1fr;
  }
}
</style>
