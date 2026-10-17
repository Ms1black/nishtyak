<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import {
  FindingList,
  describeFinding,
  formatRunCommand,
  getFindingKind,
  getFindingSection,
  isPackageBlocked,
  type AutomationQuestion,
  type Finding,
  type PackagePlan,
} from '@/entities/project'
import { DataConfirmation } from '@/features/confirm-data'
import { RunSettings } from '@/features/configure-run'
import { ProjectFileExplorer } from '@/features/exclude-files'
import { DependencyList } from '@/features/resolve-dependencies'
import { t } from '@/shared/i18n'
import { BaseButton, BaseCard, BaseIcon, CodeBlock, InlineCode, MarkdownContent } from '@/shared/ui'
import { useUploadWizardStore } from '../model/uploadWizardStore'
import ReadinessPanel from './ReadinessPanel.vue'
import SectionCard from './SectionCard.vue'
import ProjectToolsPanel from './ProjectToolsPanel.vue'
import AutomationQuestions from './AutomationQuestions.vue'
import ReplaceSourceForm from './ReplaceSourceForm.vue'

const wizard = useUploadWizardStore()
const explorer = ref<InstanceType<typeof ProjectFileExplorer> | null>(null)
const runSettings = ref<InstanceType<typeof RunSettings> | null>(null)
const dataDetails = ref<InstanceType<typeof DataConfirmation> | null>(null)
const replaceSourceOpen = ref(false)
const expanded = ref<Record<string, boolean>>({})
const advancedOpen = computed({
  get: () => wizard.advancedOpen,
  set: (value: boolean) => { wizard.advancedOpen = value },
})
const lastPlan = ref<PackagePlan | null>(wizard.plan)

watch(
  [() => wizard.project?.id, () => wizard.analysis?.analysisId],
  () => {
    expanded.value = {}
    advancedOpen.value = false
    lastPlan.value = null
    replaceSourceOpen.value = false
  },
)

watch(
  () => wizard.plan,
  (plan) => {
    if (plan) lastPlan.value = plan
  },
)

const DATA_EXTENSIONS = /\.(csv|tsv|json|xlsx?|parquet|txt|yaml|yml)$/i
const DOCUMENT_FILES = /(^|\/)(?:requirements[^/]*\.txt|readme(?:\.[^/]*)?|licen[sc]e(?:\.[^/]*)?|copying(?:\.[^/]*)?)$/i
const includedFiles = computed(() => {
  const excluded = new Set(wizard.decisions?.excludedFiles ?? [])
  const planned = wizard.plan?.files ? new Set(wizard.plan.files) : null
  return wizard.analysis?.files.filter((file) => !excluded.has(file.path) && (!planned || planned.has(file.path))) ?? []
})
const scriptFiles = computed(() =>
  includedFiles.value.filter((file) => file.path.endsWith('.py')).map((file) => file.path),
)
const dataFiles = computed(() =>
  includedFiles.value
    .filter((file) => DATA_EXTENSIONS.test(file.path) && !DOCUMENT_FILES.test(file.path))
    .map((file) => file.path),
)
const visiblePlan = computed(() => {
  const plan = wizard.plan ?? lastPlan.value
  if (!plan) return null
  const excluded = new Set(wizard.decisions?.excludedFiles ?? [])
  return {
    ...plan,
    findings: plan.findings.filter(
      (finding) => finding.code !== 'SECRET_DETECTED' || !finding.path || !excluded.has(finding.path),
    ),
  }
})
const findings = computed(() => visiblePlan.value?.findings ?? [])
const inputFindings = computed(() => findings.value.filter((finding) =>
  ['INPUT_PATH_UNVERIFIED', 'EXTERNAL_INPUT_REFERENCE', 'MISSING_DATA', 'MISSING_INPUT_FILE'].includes(finding.code),
))
const unresolvedModules = computed(() => {
  const modules: string[] = []
  for (const finding of findings.value) {
    if (getFindingKind(finding) === 'question' && finding.module) modules.push(finding.module)
  }
  return modules
})
const resolution = computed(() => visiblePlan.value?.automaticResolution)
const automationQuestions = computed(() => {
  const automation = wizard.automation
  if (automation?.phase !== 'needs_input' || isPackageBlocked(visiblePlan.value)) return []
  return automation.questions ?? (automation.question ? [automation.question] : [])
})
const progressText = computed(() => {
  const automation = wizard.automation
  if (!automation || !wizard.isAutomating) return null
  const aspect = automation.aspect ? t(`analysis.automation.aspects.${automation.aspect}`) : ''
  if (automation.phase === 'assisting') {
    return t('analysis.automation.assistingBrief', { aspect })
  }
  return t(`analysis.automation.phases.${automation.phase}`)
})
const blockingFinding = computed(() => {
  if (wizard.isAutomating) return undefined
  const blocker = findings.value.find((finding) => getFindingKind(finding) === 'blocker')
  if (blocker) return blocker
  if (!wizard.automation && resolution.value?.status === 'needs_input') {
    return findings.value.find((finding) => getFindingKind(finding) === 'question')
  }
  return undefined
})
const resolvedRequirements = computed(() =>
  wizard.lock?.packages ?? (resolution.value?.status === 'ready' ? resolution.value.packages : undefined),
)
const summaryRequirements = computed(() => resolvedRequirements.value ?? visiblePlan.value?.requirements ?? [])
const requirementsCount = computed(() => summaryRequirements.value.length)
const excludedCount = computed(() => wizard.decisions?.excludedFiles.length ?? 0)
const effectiveRun = computed(
  () => wizard.decisions?.runSpec ?? wizard.plan?.runSpec ?? wizard.analysis?.suggestedRunSpec ?? null,
)
const command = computed(() => (effectiveRun.value ? formatRunCommand(effectiveRun.value) : null))
const runSpec = computed({ get: () => effectiveRun.value, set: (value) => wizard.setRunSpec(value) })

function field<K extends 'inputDescription' | 'expectedOutput' | 'description'>(key: K) {
  return computed({ get: () => wizard.decisions![key], set: (value) => wizard.update({ [key]: value }) })
}
const inputDescription = field('inputDescription')
const expectedOutput = field('expectedOutput')
const description = field('description')

function errorCount(section: string) {
  return findings.value.filter(
    (finding) => getFindingSection(finding) === section && getFindingKind(finding) === 'blocker',
  ).length
}

async function focusSection(section: string, finding?: Finding) {
  if (section !== 'data') advancedOpen.value = true
  expanded.value[section] = true
  await nextTick()
  if (section === 'data') dataDetails.value?.openAdditions()
  if (section === 'run') runSettings.value?.edit()
  if (section === 'files' && finding?.path) explorer.value?.focusFile(finding.path)
  const element = document.getElementById(`section-${section}`)
  if (!element) return
  element.scrollIntoView({ behavior: 'smooth', block: 'start' })
  element.focus({ preventScroll: true })
}

function focusFinding(finding: Finding) {
  return focusSection(getFindingSection(finding), finding)
}

function focusQuestion(selected?: AutomationQuestion) {
  const sections = { dependencies: 'dependencies', entrypoint: 'run', description: 'data', environment: 'tools' }
  const question = selected ?? automationQuestions.value.find((item) => item.kind === 'text') ?? automationQuestions.value[0]
  if (question?.code || question?.filePath) {
    const finding = findings.value.find((item) => item.code === question.code && (!question.filePath || item.path === question.filePath))
    if (finding) return focusFinding(finding)
    if (question.filePath) return focusSection('files', {
      id: question.id, code: question.code ?? '', path: question.filePath, severity: 'warning', message: question.message ?? '',
    })
  }
  return focusSection(sections[question?.aspect ?? 'dependencies'])
}

function toggleAdvanced(event: Event) {
  if (event.target === event.currentTarget) advancedOpen.value = (event.target as HTMLDetailsElement).open
}
</script>

<template>
  <div v-if="wizard.project" class="analysis">
    <BaseCard
      v-if="(wizard.isLoading || wizard.isAnalyzing) && !wizard.error"
      padding="lg"
      class="analysis__state"
    >
      <BaseIcon name="refresh" :size="32" spin />
      <h2 class="analysis__state-title">
        {{ $t('analysis.state.analyzingTitle', { name: wizard.project.name }) }}
      </h2>
      <p class="analysis__state-text">{{ progressText ?? $t('analysis.state.analyzingText') }}</p>
    </BaseCard>
    <BaseCard
      v-else-if="
        wizard.analysis?.status === 'failed' ||
        (wizard.error && (!wizard.analysis || wizard.isAnalyzing || !wizard.decisions || (!wizard.plan && wizard.saveState === 'idle')))
      "
      padding="lg"
      class="analysis__state"
    >
      <BaseIcon name="alert" :size="32" class="analysis__state-error" />
      <h2 class="analysis__state-title">{{ $t('analysis.state.failedTitle') }}</h2>
      <p class="analysis__state-text">{{ wizard.analysis?.error ?? (wizard.error && $t(wizard.error)) }}</p>
      <BaseButton variant="tonal" icon="refresh" @click="wizard.loadAnalysis()">{{
        $t('common.retry')
      }}</BaseButton>
      <BaseButton variant="tonal" icon="upload" @click="wizard.reset()">{{
        $t('analysis.state.uploadOther')
      }}</BaseButton>
    </BaseCard>

    <div v-else-if="wizard.analysis && wizard.decisions" class="analysis__grid">
      <div class="analysis__main">
        <BaseCard padding="lg" class="overview">
          <h2 class="overview__title">{{ $t('analysis.review.title') }}</h2>
          <dl class="overview__facts">
            <div>
              <dt>{{ $t('analysis.review.sources') }}</dt>
              <dd>{{ $t('units.files', includedFiles.length) }}</dd>
            </div>
            <div>
              <dt>{{ $t('analysis.review.packages') }}</dt>
              <dd>{{ requirementsCount }}</dd>
            </div>
            <div v-if="resolution?.target || visiblePlan?.pythonVersion">
              <dt>Python</dt>
              <dd>{{ resolution?.target?.pythonVersion ?? visiblePlan?.pythonVersion }}</dd>
            </div>
          </dl>
          <div class="overview__run">
            <div class="overview__run-head">
              <span>{{ $t('analysis.run.command') }}</span>
              <button v-if="wizard.canEdit" type="button" class="overview__link" @click="focusSection('run')">
                {{ $t('analysis.review.editRun') }}
              </button>
            </div>
            <CodeBlock v-if="command" :code="command" language="bash" :line-numbers="false" />
            <p v-else>{{ $t('analysis.review.runUnknown') }}</p>
          </div>
          <div v-if="visiblePlan?.description" class="overview__description">
            <div class="overview__run-head">
              <span>{{ $t('analysis.review.descriptionTitle') }}</span>
              <button v-if="wizard.canEdit" type="button" class="overview__link" @click="focusSection('data')">
                {{ $t('analysis.review.editRun') }}
              </button>
            </div>
            <MarkdownContent class="overview__description-text" :content="visiblePlan.description" />
          </div>
          <div class="overview__environment" aria-live="polite">
            <div class="overview__run-head">
              <span>{{ $t('analysis.automatic.environmentTitle') }}</span>
              <BaseIcon v-if="wizard.isPreparingEnvironment || wizard.isAutomating" name="refresh" :size="16" spin />
              <BaseIcon v-else-if="resolvedRequirements && wizard.automation?.phase !== 'needs_input'" name="check" :size="16" />
            </div>
            <template v-if="wizard.isPreparingEnvironment || wizard.isAutomating">
              <p>{{ progressText ?? $t('analysis.automatic.resolving') }}</p>
            </template>
            <template v-else>
              <ul v-if="summaryRequirements.length" class="overview__requirements">
                <li v-for="requirement in summaryRequirements" :key="requirement"><InlineCode :code="requirement" language="plaintext" /></li>
              </ul>
              <p v-else>{{ $t('analysis.deps.empty') }}</p>
              <p v-if="resolvedRequirements && wizard.automation?.phase !== 'needs_input'" class="overview__hint">
                {{ $t('analysis.automatic.resolved', { platform: wizard.lock?.target.platform ?? resolution?.target?.platform ?? '' }) }}
              </p>
              <p v-else-if="!wizard.automation && resolution?.status === 'needs_input'" class="overview__hint">
                {{ $t('analysis.automatic.needsInput') }}
              </p>
              <p v-else-if="resolution?.status === 'unavailable' || resolution?.status === 'disabled'" class="overview__hint">
                {{ $t('analysis.automatic.unavailable') }}
              </p>
            </template>
          </div>
        </BaseCard>

        <BaseCard v-if="automationQuestions.length" padding="lg" class="questions">
          <AutomationQuestions
            :questions="automationQuestions"
            :questionnaire-id="wizard.automation?.questionnaireId"
            :project-id="wizard.project.id"
            :analysis-id="wizard.analysis.analysisId"
            :candidates="wizard.analysis.launchCandidates ?? []"
            :disabled="wizard.isBusy || !wizard.canEdit"
            @answer="wizard.answerQuestions"
            @open-settings="focusQuestion"
          />
        </BaseCard>
        <BaseCard v-else-if="blockingFinding" padding="lg" class="questions" role="alert">
          <h3 class="questions__title">{{ $t('analysis.automatic.blockingTitle') }}</h3>
          <p class="overview__text">{{ describeFinding(blockingFinding) }}</p>
          <BaseButton v-if="wizard.canEdit" variant="tonal" :disabled="wizard.isBusy" @click="focusFinding(blockingFinding)">
            {{ $t('analysis.automatic.fix') }}
          </BaseButton>
        </BaseCard>

        <SectionCard
          id="section-data"
          :title="$t('analysis.data.title')"
        >
          <DataConfirmation
            ref="dataDetails"
            v-model:input-description="inputDescription"
            v-model:expected-output="expectedOutput"
            v-model:description="description"
            :data-files="dataFiles"
            :input-findings="inputFindings"
            :can-replace="wizard.canEdit"
            :disabled="wizard.isBusy || !wizard.canEdit"
            @replace-source="replaceSourceOpen = !replaceSourceOpen"
          />
          <ReplaceSourceForm v-if="replaceSourceOpen && wizard.canEdit" :disabled="wizard.isBusy" @replace="wizard.replaceSource" />
        </SectionCard>

        <p v-if="!wizard.canEdit" class="analysis__optional">{{ $t('teams.readOnly') }}</p>
        <details class="analysis__advanced" :open="advancedOpen" @toggle="toggleAdvanced">
          <summary>{{ $t('analysis.review.expertTitle') }}</summary>

          <SectionCard
            id="section-dependencies"
            v-model:open="expanded.dependencies"
            collapsible
            :title="$t('analysis.deps.title')"
            :errors="errorCount('dependencies')"
          >
            <DependencyList
              editor
              :dependencies="wizard.analysis.dependencies"
              :requirements="visiblePlan?.requirements"
              :unresolved-modules="unresolvedModules"
              :packages="wizard.decisions.packages"
              :versions="wizard.decisions.versions"
              :excluded-modules="wizard.decisions.excludedModules"
              :disabled="wizard.isBusy || !wizard.canEdit"
              @set-package="wizard.setPackage"
              @set-version="wizard.setVersion"
              @toggle="wizard.toggleModule"
            />
          </SectionCard>

          <SectionCard
            id="section-run"
            v-model:open="expanded.run"
            collapsible
            :title="$t('analysis.review.runSettings')"
            :errors="errorCount('run')"
          >
            <RunSettings
              ref="runSettings"
              v-model:run-spec="runSpec"
              :has-run-override="!!wizard.decisions.runSpec"
              :entrypoints="wizard.analysis.entrypoints"
              :script-files="scriptFiles"
              :candidates="wizard.analysis.launchCandidates"
              :disabled="wizard.isBusy || !wizard.canEdit"
            />
          </SectionCard>
          <SectionCard
            id="section-files"
            v-model:open="expanded.files"
            collapsible
            :title="$t('analysis.files.title')"
            :subtitle="$t('analysis.review.filesHint', { excluded: excludedCount })"
            :errors="errorCount('files')"
          >
            <ProjectFileExplorer
              v-if="expanded.files"
              ref="explorer"
              :project-id="wizard.project.id"
              :analysis-id="wizard.analysis.analysisId"
              :project-name="wizard.project.name"
              :files="wizard.analysis.files"
              :excluded-files="wizard.decisions.excludedFiles"
              :disabled="wizard.isBusy || !wizard.canEdit"
              @toggle="wizard.toggleFile"
            />
            <ReplaceSourceForm v-if="wizard.canEdit" :disabled="wizard.isBusy" @replace="wizard.replaceSource" />
          </SectionCard>
          <SectionCard
            v-if="wizard.canEdit"
            id="section-tools"
            v-model:open="expanded.tools"
            collapsible
            :title="$t('workflow.toolsTitle')"
          >
            <ProjectToolsPanel />
          </SectionCard>
          <SectionCard
            v-if="findings.length"
            id="section-notes"
            v-model:open="expanded.notes"
            collapsible
            :title="$t('analysis.review.reportTitle')"
          >
            <FindingList :findings="findings" actionable @select="focusFinding" />
          </SectionCard>
        </details>
      </div>

      <ReadinessPanel
        v-if="wizard.canEdit"
        class="analysis__aside"
        :project="wizard.project"
        :plan="visiblePlan"
        :save-state="wizard.saveState"
        :saved-at="wizard.savedAt"
        :is-building="wizard.isBuilding"
        :is-preparing-environment="wizard.isPreparingEnvironment"
        :is-processing="wizard.isAutomating"
        :progress-text="progressText"
        :is-refreshing="wizard.isRefreshing"
        :can-build="wizard.canBuild"
        :error="wizard.error && $t(wizard.error)"
        @build="wizard.build()"
        @reset="wizard.reset()"
        @reload="wizard.reloadDecisions()"
        @refresh="wizard.refreshPlan()"
      />
    </div>
  </div>
</template>

<style scoped>
.questions {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.questions__title {
  font-family: var(--font-display);
  font-size: 20px;
  font-weight: 500;
}
.questions > :deep(.button) {
  align-self: flex-start;
}
.analysis__advanced {
  padding: 20px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
}
.analysis__advanced > summary {
  cursor: pointer;
  font-size: 14px;
  font-weight: 500;
}
.analysis__advanced > .section {
  margin-top: 16px;
}
.overview__environment {
  margin-top: 20px;
  padding-top: 18px;
  border-top: 1px solid var(--color-border);
  font-size: 14px;
}
.overview__requirements {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  list-style: none;
  padding: 0;
  margin: 0;
}
.overview__requirements li {
  padding: 6px 10px;
  border-radius: var(--radius-sm);
  background: var(--color-bg);
  overflow-wrap: anywhere;
  font-size: 12px;
}
.overview__description {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 20px;
  padding-top: 18px;
  border-top: 1px solid var(--color-border);
}
.overview__description-text {
  color: var(--color-text-secondary);
  font-size: 14px;
  line-height: 1.6;
}
.analysis__grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 340px;
  gap: 24px;
  align-items: start;
}
.analysis__main {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}
.analysis__aside {
  position: sticky;
  top: 24px;
}
.analysis__optional {
  margin: 12px 4px 0;
  color: var(--color-text-muted);
  font-size: 13px;
}
.overview__title {
  font-family: var(--font-display);
  font-size: 26px;
  font-weight: 500;
  line-height: 1.25;
}
.overview__text {
  margin-top: 12px;
  color: var(--color-text-secondary);
  font-size: 14px;
  line-height: 1.6;
}
.overview__facts {
  display: flex;
  flex-wrap: wrap;
  gap: 16px 40px;
  margin: 24px 0;
}
.overview__facts dt {
  color: var(--color-text-muted);
  font-size: 12px;
}
.overview__facts dd {
  margin: 5px 0 0;
  font-size: 16px;
}
.overview__run {
  padding: 16px;
  border-radius: var(--radius-md);
  background: var(--color-bg);
  font-size: 13px;
}
.overview__run-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 10px;
  color: var(--color-text-muted);
}
.overview__run code {
  overflow-wrap: anywhere;
  font-size: 14px;
}
.overview__hint {
  margin-top: 10px;
  font-size: 13px;
  color: var(--color-text-muted);
  line-height: 1.5;
}
.overview__link {
  color: var(--color-text-secondary);
  font-size: 13px;
  text-decoration: underline;
  text-underline-offset: 3px;
}
.analysis__state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding-block: 56px;
  text-align: center;
}
.analysis__state-title {
  margin-top: 8px;
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 500;
}
.analysis__state-text {
  max-width: 520px;
  margin-bottom: 8px;
  color: var(--color-text-secondary);
  font-size: 14px;
  line-height: 1.5;
}
.analysis__state-error {
  color: var(--color-danger);
}
@media (max-width: 1100px) {
  .analysis__grid {
    grid-template-columns: 1fr;
  }
  .analysis__aside {
    position: static;
    order: -1;
  }
}
@media (max-width: 640px) {
  .overview__title {
    font-size: 23px;
  }
}
</style>
