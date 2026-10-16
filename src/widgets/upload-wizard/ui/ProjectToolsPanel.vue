<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { projectApi, type AdviceFeature, type AdviceSuggestion, type TargetPlatform } from '@/entities/project'
import { ApiError } from '@/shared/api'
import { t } from '@/shared/i18n'
import { downloadBlob } from '@/shared/lib'
import { BaseButton, BaseCheckbox, BaseInput, BaseSelect, BaseTextarea, type SelectOption } from '@/shared/ui'
import { useUploadWizardStore } from '../model/uploadWizardStore'

const wizard = useUploadWizardStore()
const projectRoot = ref('')
const sourceRoots = ref('.')
const profilePython = ref<string | null>(null)
const targetPython = ref<string | null>(null)
const platform = ref<TargetPlatform | null>('linux-x86_64')
const feature = ref<AdviceFeature | null>('readme')
const prompt = ref('')
const sourceConsent = ref(false)
const environment = ref<unknown>()
const environmentName = ref('')
const localError = ref<string | null>(null)
const applied = ref(new Set<number>())
const isGraphLoading = ref(false)

const pythonOptions: SelectOption[] = Array.from({ length: 7 }, (_, index) => {
  const version = `3.${index + 8}`
  return { value: version, label: `Python ${version}` }
})
const platformOptions: SelectOption<TargetPlatform>[] = [
  { value: 'linux-x86_64', label: 'Linux x86_64' },
  { value: 'linux-aarch64', label: 'Linux ARM64' },
  { value: 'macos-arm64', label: 'macOS Apple Silicon' },
  { value: 'macos-x86_64', label: 'macOS Intel' },
  { value: 'windows-x86_64', label: 'Windows x86_64' },
]
const featureOptions = computed<SelectOption<AdviceFeature>[]>(() => [
  { value: 'readme', label: t('workflow.adviceReadme') },
  { value: 'entrypoint', label: t('workflow.adviceEntrypoint') },
  { value: 'packages', label: t('workflow.advicePackages') },
  { value: 'explain', label: t('workflow.adviceExplain') },
])
const rootHints = computed(() => wizard.analysis?.projectRoots?.join(', ') ?? '.')
const targetMatchesProfile = computed(() => targetPython.value === wizard.analysis?.options?.pythonVersion)

watch(
  () => wizard.analysis?.analysisId,
  () => {
    const options = wizard.analysis?.options
    projectRoot.value = options?.projectRoot ?? ''
    sourceRoots.value = options?.sourceRoots.join(', ') ?? '.'
    profilePython.value = options?.pythonVersion ?? '3.11'
    targetPython.value = wizard.decisions?.pythonVersion ?? options?.pythonVersion ?? '3.11'
    environment.value = undefined
    environmentName.value = ''
    sourceConsent.value = false
    localError.value = null
  },
  { immediate: true },
)

watch(
  () => wizard.advice,
  () => {
    applied.value = new Set()
  },
)

async function readEnvironment(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  environment.value = undefined
  environmentName.value = ''
  localError.value = null
  if (!file) return
  try {
    if (file.size > 256 * 1024) throw new Error(t('workflow.environmentTooLarge'))
    const value: unknown = JSON.parse(await file.text())
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(t('workflow.environmentInvalid'))
    environment.value = value
    environmentName.value = file.name
  } catch (cause) {
    localError.value = cause instanceof SyntaxError ? t('workflow.environmentInvalid') : (cause as Error).message
  }
}

async function reanalyze() {
  if (!profilePython.value) return
  localError.value = null
  const roots = sourceRoots.value
    .split(',')
    .map((root) => root.trim())
    .filter(Boolean)
  await wizard.reanalyze(
    {
      pythonVersion: profilePython.value,
      projectRoot: projectRoot.value.trim() || null,
      sourceRoots: roots.length ? roots : ['.'],
    },
    environment.value,
  )
}

function canApply(suggestion: AdviceSuggestion): boolean {
  if (suggestion.kind === 'readme_description') return true
  if (suggestion.kind === 'package_hypothesis') return !!suggestion.module && !!suggestion.package
  if (suggestion.kind === 'entrypoint_hypothesis') {
    return !!wizard.analysis?.launchCandidates?.some((candidate) => candidate.id === suggestion.candidate_id)
  }
  return false
}

function apply(suggestion: AdviceSuggestion, index: number) {
  wizard.applySuggestion(suggestion)
  applied.value = new Set([...applied.value, index])
}

async function downloadGraph() {
  if (!wizard.project || isGraphLoading.value) return
  const projectId = wizard.project.id
  isGraphLoading.value = true
  localError.value = null
  try {
    const graph = await projectApi.getGraph(projectId)
    if (wizard.project?.id !== projectId) return
    downloadBlob(new Blob([JSON.stringify(graph, null, 2)], { type: 'application/json' }), 'import-graph.json')
  } catch (cause) {
    localError.value = cause instanceof ApiError ? cause.message : t('workflow.graphError')
  } finally {
    isGraphLoading.value = false
  }
}
</script>

<template>
  <div class="tools">
    <details class="tools__section">
      <summary>{{ $t('workflow.analysisSettings') }}</summary>
      <div class="tools__body">
        <p class="tools__hint">{{ $t('workflow.reanalysisWarning') }}</p>
        <label class="tools__field">
          <span>{{ $t('workflow.profilePython') }}</span>
          <BaseSelect v-model="profilePython" :options="pythonOptions" :disabled="wizard.isBusy" />
        </label>
        <label class="tools__field">
          <span>{{ $t('workflow.projectRoot') }}</span>
          <BaseInput v-model="projectRoot" mono :placeholder="$t('workflow.autoRoot')" :disabled="wizard.isBusy" />
          <span class="tools__hint">{{ $t('workflow.rootCandidates', { roots: rootHints }) }}</span>
        </label>
        <label class="tools__field">
          <span>{{ $t('workflow.sourceRoots') }}</span>
          <BaseInput v-model="sourceRoots" mono placeholder="., src" :disabled="wizard.isBusy" />
        </label>
        <label class="tools__field">
          <span>{{ $t('workflow.authorEnvironment') }}</span>
          <input type="file" accept=".json,application/json" :disabled="wizard.isBusy" @change="readEnvironment" />
          <span class="tools__hint">{{ environmentName || $t('workflow.environmentHint') }}</span>
        </label>
        <BaseButton variant="tonal" :disabled="wizard.isBusy || !profilePython" @click="reanalyze">{{
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
          :disabled="wizard.isBusy"
          @update:model-value="wizard.toggleExtra(group)"
          >{{ group }}</BaseCheckbox
        >
      </div>
    </div>

    <details class="tools__section">
      <summary>{{ $t('workflow.lockTitle') }}</summary>
      <div class="tools__body">
        <p class="tools__hint">{{ $t('workflow.lockHint') }}</p>
        <div class="tools__row">
          <BaseSelect
            v-model="targetPython"
            :options="pythonOptions"
            :aria-label="$t('workflow.targetPython')"
            :disabled="wizard.isBusy"
          />
          <BaseSelect
            v-model="platform"
            :options="platformOptions"
            :aria-label="$t('workflow.targetPlatform')"
            :disabled="wizard.isBusy"
          />
        </div>
        <p v-if="!targetMatchesProfile" class="tools__hint">{{ $t('workflow.profileMismatch') }}</p>
        <BaseButton
          variant="tonal"
          :disabled="wizard.isBusy || !platform || !targetPython || !targetMatchesProfile"
          :loading="wizard.isResolving"
          @click="targetPython && platform && wizard.resolve({ pythonVersion: targetPython, platform })"
          >{{ $t('workflow.resolve') }}</BaseButton
        >
        <div v-if="wizard.lock" class="tools__result">
          <p>{{ $t('workflow.lockReady', { count: wizard.lock.packages.length }) }}</p>
          <code v-for="requirement in wizard.lock.packages" :key="requirement">{{ requirement }}</code>
          <p class="tools__hint">{{ $t('workflow.lockNotExecution') }}</p>
        </div>
      </div>
    </details>

    <details class="tools__section">
      <summary>{{ $t('workflow.adviceTitle') }}</summary>
      <div class="tools__body">
        <p class="tools__hint">{{ $t('workflow.adviceHint') }}</p>
        <BaseSelect
          v-model="feature"
          :options="featureOptions"
          :disabled="wizard.isBusy || wizard.isAdvising"
          :aria-label="$t('workflow.adviceTask')"
        />
        <BaseTextarea
          v-model="prompt"
          :maxlength="4000"
          :placeholder="$t('workflow.advicePrompt')"
          :disabled="wizard.isBusy || wizard.isAdvising"
        />
        <BaseCheckbox v-model="sourceConsent" :disabled="wizard.isAdvising">{{
          $t('workflow.sourceConsent')
        }}</BaseCheckbox>
        <BaseButton
          variant="tonal"
          :disabled="!sourceConsent || !feature || wizard.isBusy || wizard.isAdvising"
          :loading="wizard.isAdvising"
          @click="feature && wizard.requestAdvice(feature, prompt)"
          >{{ $t('workflow.askAdvice') }}</BaseButton
        >
        <div v-if="wizard.advice" class="tools__result" aria-live="polite">
          <p v-if="wizard.advice.status !== 'ok'">{{ $t('workflow.adviceUnavailable') }} {{ wizard.advice.reason }}</p>
          <template v-else>
            <p class="tools__hint">
              {{ $t('workflow.adviceStats', { rounds: wizard.advice.rounds, tools: wizard.advice.tool_calls }) }}
            </p>
            <div v-for="(suggestion, index) in wizard.advice.suggestions" :key="index" class="tools__suggestion">
              <p>{{ suggestion.text }}</p>
              <BaseButton
                v-if="canApply(suggestion)"
                variant="tonal"
                size="sm"
                :disabled="wizard.isBusy || applied.has(index)"
                @click="apply(suggestion, index)"
                >{{ applied.has(index) ? $t('workflow.applied') : $t('workflow.apply') }}</BaseButton
              >
            </div>
            <ul v-if="wizard.advice.unknowns.length">
              <li v-for="unknown in wizard.advice.unknowns" :key="unknown">{{ unknown }}</li>
            </ul>
          </template>
        </div>
      </div>
    </details>

    <BaseCheckbox
      v-model="wizard.includeDockerfile"
      :disabled="wizard.isBusy || (!wizard.canIncludeDockerfile && !wizard.includeDockerfile)"
      >{{ $t('workflow.dockerfile') }}</BaseCheckbox
    >
    <p v-if="!wizard.canIncludeDockerfile" class="tools__hint">{{ $t('workflow.dockerUnavailable') }}</p>
    <BaseButton variant="tonal" :disabled="wizard.isBusy" :loading="isGraphLoading" @click="downloadGraph">{{
      $t('workflow.downloadGraph')
    }}</BaseButton>
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
.tools__suggestion {
  display: flex;
  flex-direction: column;
  gap: 10px;
  white-space: pre-wrap;
  line-height: 1.5;
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
