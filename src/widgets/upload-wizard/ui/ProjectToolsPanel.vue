<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { TargetPlatform } from '@/entities/project'
import { t } from '@/shared/i18n'
import { BaseButton, BaseCheckbox, BaseInput, BaseSelect, CodeBlock, type SelectOption } from '@/shared/ui'
import { useUploadWizardStore } from '../model/uploadWizardStore'

const wizard = useUploadWizardStore()
const projectRoot = ref('')
const sourceRoots = ref('.')
const targetPython = ref<string | null>(null)
const platform = ref<TargetPlatform | null>('linux-x86_64')
const environment = ref<unknown>()
const environmentName = ref('')
const localError = ref<string | null>(null)
let environmentRead = 0
let targetDirty = false

const pythonOptions = computed<SelectOption[]>(() => {
  const versions = Array.from({ length: 7 }, (_, index) => `3.${index + 8}`)
  if (targetPython.value && !versions.includes(targetPython.value)) versions.push(targetPython.value)
  return versions.map((version) => ({ value: version, label: `Python ${version}` }))
})
const platformOptions: SelectOption<TargetPlatform>[] = [
  { value: 'linux-x86_64', label: 'Linux x86_64' },
  { value: 'linux-aarch64', label: 'Linux ARM64' },
  { value: 'macos-arm64', label: 'macOS Apple Silicon' },
  { value: 'macos-x86_64', label: 'macOS Intel' },
  { value: 'windows-x86_64', label: 'Windows x86_64' },
]
const rootHints = computed(() => wizard.analysis?.projectRoots?.join(', ') ?? '.')
const waitingForAnswers = computed(() => wizard.automation?.phase === 'needs_input')
const disabled = computed(() => wizard.isBusy || !wizard.canEdit)
const targetChanged = computed(() => !wizard.targetEnvironment ||
  targetPython.value !== wizard.targetEnvironment.pythonVersion || platform.value !== wizard.targetEnvironment.platform,
)

watch(
  () => [wizard.project?.id, wizard.analysis?.analysisId, wizard.targetEnvironment?.pythonVersion, wizard.targetEnvironment?.platform],
  (value, previous) => {
    if (!previous || value[0] !== previous[0] || value[1] !== previous[1]) targetDirty = false
    if (targetDirty) return
    targetPython.value = wizard.targetEnvironment?.pythonVersion ?? wizard.plan?.pythonVersion ??
      wizard.decisions?.pythonVersion ?? wizard.analysis?.options?.pythonVersion ?? '3.11'
    platform.value = wizard.targetEnvironment?.platform ?? 'linux-x86_64'
  },
  { immediate: true },
)

watch(
  () => wizard.analysis?.analysisId,
  () => {
    environmentRead += 1
    const options = wizard.analysis?.options
    projectRoot.value = options?.projectRoot ?? ''
    sourceRoots.value = options?.sourceRoots.join(', ') ?? '.'
    environment.value = undefined
    environmentName.value = ''
    localError.value = null
  },
  { immediate: true },
)

async function readEnvironment(event: Event) {
  if (disabled.value) return
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  const request = ++environmentRead
  input.value = ''
  environment.value = undefined
  environmentName.value = ''
  localError.value = null
  if (!file) return
  try {
    if (file.size > 256 * 1024) throw new Error(t('workflow.environmentTooLarge'))
    const value: unknown = JSON.parse(await file.text())
    if (request !== environmentRead) return
    if (!value || typeof value !== 'object' || Array.isArray(value))
      throw new Error(t('workflow.environmentInvalid'))
    environment.value = value
    environmentName.value = file.name
  } catch (cause) {
    if (request !== environmentRead) return
    localError.value =
      cause instanceof SyntaxError ? t('workflow.environmentInvalid') : (cause as Error).message
  }
}

async function reanalyze() {
  const options = wizard.analysis?.options
  if (!options || disabled.value || waitingForAnswers.value) return
  localError.value = null
  const roots = sourceRoots.value
    .split(',')
    .map((root) => root.trim())
    .filter(Boolean)
  await wizard.reanalyze(
    {
      pythonVersion: options.pythonVersion,
      targetPlatform: wizard.targetEnvironment?.platform ?? options.targetPlatform,
      projectRoot: projectRoot.value.trim() || null,
      sourceRoots: roots.length ? roots : ['.'],
    },
    environment.value,
  )
}

async function applyTarget() {
  if (!targetPython.value || !platform.value || disabled.value || waitingForAnswers.value) return
  await wizard.applyTarget({ pythonVersion: targetPython.value, platform: platform.value })
}
</script>

<template>
  <div class="tools">
    <section class="tools__section" aria-labelledby="target-environment-title">
      <h3 id="target-environment-title">{{ $t('workflow.targetTitle') }}</h3>
      <div class="tools__body">
        <div class="tools__row">
          <label class="tools__field">
            <span>{{ $t('workflow.targetPython') }}</span>
            <BaseSelect
              v-model="targetPython"
              :options="pythonOptions"
              :aria-label="$t('workflow.targetPython')"
              :disabled="disabled || waitingForAnswers"
              @update:model-value="targetDirty = true"
            />
          </label>
          <label class="tools__field">
            <span>{{ $t('workflow.targetPlatform') }}</span>
            <BaseSelect
              v-model="platform"
              :options="platformOptions"
              :aria-label="$t('workflow.targetPlatform')"
              :disabled="disabled || waitingForAnswers"
              @update:model-value="targetDirty = true"
            />
          </label>
        </div>
        <p v-if="wizard.analysis?.pythonRequires" class="tools__hint">{{ $t('workflow.pythonRequires', { spec: wizard.analysis.pythonRequires }) }}</p>
        <p v-if="waitingForAnswers" class="tools__hint">{{ $t('workflow.targetAfterQuestions') }}</p>
        <BaseButton
          v-if="wizard.canEdit"
          variant="tonal"
          :disabled="disabled || waitingForAnswers || !platform || !targetPython || !targetChanged"
          :loading="wizard.isRefreshing"
          @click="applyTarget"
          >{{ $t('workflow.applyTarget') }}</BaseButton
        >
      </div>
    </section>

    <details class="tools__section">
      <summary>{{ $t('workflow.analysisSettings') }}</summary>
      <div class="tools__body">
        <p class="tools__hint">{{ $t('workflow.reanalysisWarning') }}</p>
        <p class="tools__hint">{{ $t('workflow.analysisProfile', { version: wizard.analysis?.options?.pythonVersion ?? '—' }) }}</p>
        <label class="tools__field">
          <span>{{ $t('workflow.projectRoot') }}</span>
          <BaseInput
            v-model="projectRoot"
            mono
            :placeholder="$t('workflow.autoRoot')"
            :disabled="disabled || waitingForAnswers"
          />
          <span class="tools__hint">{{ $t('workflow.rootCandidates', { roots: rootHints }) }}</span>
        </label>
        <label class="tools__field">
          <span>{{ $t('workflow.sourceRoots') }}</span>
          <BaseInput v-model="sourceRoots" mono placeholder="., src" :disabled="disabled || waitingForAnswers" />
        </label>
        <label class="tools__field">
          <span>{{ $t('workflow.authorEnvironment') }}</span>
          <input
            type="file"
            accept=".json,application/json"
            :disabled="disabled || waitingForAnswers"
            @change="readEnvironment"
          />
          <span class="tools__hint">{{ environmentName || $t('workflow.environmentHint') }}</span>
        </label>
        <BaseButton v-if="wizard.canEdit" variant="tonal" :disabled="disabled || waitingForAnswers || !wizard.analysis?.options" @click="reanalyze">{{
          $t('workflow.reanalyze')
        }}</BaseButton>
      </div>
    </details>

    <div v-if="wizard.analysis?.optionalGroups?.length" class="tools__section">
      <h3>{{ $t('workflow.extras') }}</h3>
      <div class="tools__body">
        <BaseCheckbox
          v-for="group in wizard.analysis.optionalGroups"
          :key="group"
          :model-value="wizard.decisions?.selectedExtras.includes(group) ?? false"
          :disabled="disabled"
          @update:model-value="wizard.toggleExtra(group)"
          >{{ group }}</BaseCheckbox
        >
      </div>
    </div>

    <details v-if="wizard.lock" class="tools__section">
      <summary>{{ $t('workflow.environmentDetails') }}</summary>
      <div class="tools__body">
        <div class="tools__result">
          <p>{{ $t('workflow.lockReady', { count: wizard.lock.packages.length }) }}</p>
          <CodeBlock :code="wizard.lock.requirements" language="plaintext" filename="requirements.lock.txt" />
        </div>
      </div>
    </details>

    <p class="tools__hint">{{ $t(wizard.canIncludeDockerfile ? 'workflow.dockerAutomatic' : 'workflow.dockerUnavailable') }}</p>
    <p v-if="localError" class="tools__error" role="alert">{{ localError }}</p>
  </div>
</template>

<style scoped>
.tools {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.tools__section {
  border-bottom: 1px solid var(--color-border);
  padding-bottom: 18px;
}
.tools__section summary {
  cursor: pointer;
  font-weight: 500;
}
.tools__section h3 {
  font-size: 15px;
  font-weight: 500;
}
.tools__body,
.tools__field {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.tools__body {
  margin-top: 16px;
}
.tools__field {
  gap: 8px;
  font-size: 14px;
}
.tools__row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.tools__hint {
  color: var(--color-text-muted);
  font-size: 13px;
  line-height: 1.5;
}
.tools__result {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  border-radius: var(--radius-md);
  background: var(--color-bg);
  font-size: 14px;
}
.tools__result code {
  font-size: 12px;
  overflow-wrap: anywhere;
}
.tools__result ul {
  list-style: disc;
  padding-left: 20px;
}
.tools__error {
  color: var(--color-danger);
  font-size: 13px;
}
@media (max-width: 640px) {
  .tools__row {
    grid-template-columns: 1fr;
  }
}
</style>
