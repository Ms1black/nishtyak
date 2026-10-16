<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { FindingList, getFindingKind, getFindingSection, type Finding } from '@/entities/project'
import { DataConfirmation } from '@/features/confirm-data'
import { RunSettings } from '@/features/configure-run'
import { ProjectFileExplorer } from '@/features/exclude-files'
import { DependencyList } from '@/features/resolve-dependencies'
import { BaseButton, BaseCard, BaseIcon } from '@/shared/ui'
import { useUploadWizardStore } from '../model/uploadWizardStore'
import ReadinessPanel from './ReadinessPanel.vue'
import SectionCard from './SectionCard.vue'
import ProjectToolsPanel from './ProjectToolsPanel.vue'

const wizard = useUploadWizardStore()
const explorer = ref<InstanceType<typeof ProjectFileExplorer> | null>(null)

const DATA_EXTENSIONS = /\.(csv|tsv|json|xlsx?|parquet|txt|yaml|yml)$/i

const includedFiles = computed(() => {
  const excluded = new Set(wizard.decisions?.excludedFiles ?? [])
  return wizard.analysis?.files.filter((f) => !excluded.has(f.path)) ?? []
})

const scriptFiles = computed(() => includedFiles.value.filter((f) => f.path.endsWith('.py')).map((f) => f.path))
const dataFiles = computed(() =>
  includedFiles.value
    .filter((f) => DATA_EXTENSIONS.test(f.path) && !/(^|\/)requirements[^/]*\.txt$/.test(f.path))
    .map((f) => f.path),
)

const visiblePlan = computed(() => {
  if (!wizard.plan) return null
  const excluded = new Set(wizard.decisions?.excludedFiles ?? [])
  return {
    ...wizard.plan,
    findings: wizard.plan.findings.filter(
      (finding) => finding.code !== 'SECRET_DETECTED' || !finding.path || !excluded.has(finding.path),
    ),
  }
})

function countIn(section: string, kind: 'blocker' | 'question') {
  return (
    visiblePlan.value?.findings.filter((f) => getFindingSection(f) === section && getFindingKind(f) === kind).length ??
    0
  )
}

const notes = computed(() => visiblePlan.value?.findings.filter((f) => getFindingKind(f) === 'note') ?? [])
const requirementsCount = computed(() => wizard.plan?.requirements.length ?? 0)

const runSpec = computed({
  get: () => wizard.decisions?.runSpec ?? wizard.plan?.runSpec ?? wizard.analysis?.suggestedRunSpec ?? null,
  set: (value) => wizard.setRunSpec(value),
})

function field<K extends 'pythonVersion' | 'dataConfirmed' | 'inputDescription' | 'expectedOutput' | 'description'>(
  key: K,
) {
  return computed({
    get: () => wizard.decisions![key],
    set: (value) => wizard.update({ [key]: value }),
  })
}

const pythonVersion = field('pythonVersion')
const dataConfirmed = field('dataConfirmed')
const inputDescription = field('inputDescription')
const expectedOutput = field('expectedOutput')
const description = field('description')

const SECTION_ID: Record<string, string> = {
  files: 'section-files',
  dependencies: 'section-dependencies',
  run: 'section-run',
  data: 'section-data',
  notes: 'section-notes',
}

async function focusFinding(finding: Finding) {
  const section = getFindingSection(finding)
  if (section === 'files' && finding.path) explorer.value?.focusFile(finding.path)
  await nextTick()
  const el = document.getElementById(SECTION_ID[section] ?? 'section-notes')
  if (!el) return
  el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  el.focus({ preventScroll: true })
  el.classList.add('is-highlighted')
  setTimeout(() => el.classList.remove('is-highlighted'), 1200)
}
</script>

<template>
  <div v-if="wizard.project" class="analysis">
    <BaseCard v-if="(wizard.isLoading || wizard.isAnalyzing) && !wizard.error" padding="lg" class="analysis__state">
      <BaseIcon name="refresh" :size="32" spin />
      <h2 class="analysis__state-title">{{ $t('analysis.state.analyzingTitle', { name: wizard.project.name }) }}</h2>
      <p class="analysis__state-text">{{ $t('analysis.state.analyzingText') }}</p>
      <div class="analysis__skeleton" aria-hidden="true">
        <span v-for="n in 4" :key="n" />
      </div>
    </BaseCard>

    <BaseCard
      v-else-if="
        wizard.analysis?.status === 'failed' ||
        (wizard.error && (!wizard.analysis || wizard.isAnalyzing || !wizard.decisions))
      "
      padding="lg"
      class="analysis__state"
    >
      <BaseIcon name="alert" :size="32" class="analysis__state-error" />
      <h2 class="analysis__state-title">{{ $t('analysis.state.failedTitle') }}</h2>
      <p class="analysis__state-text">{{ wizard.analysis?.error ?? (wizard.error && $t(wizard.error)) }}</p>
      <BaseButton variant="tonal" icon="refresh" @click="wizard.loadAnalysis()">{{ $t('common.retry') }}</BaseButton>
      <BaseButton variant="tonal" icon="upload" @click="wizard.reset()">{{
        $t('analysis.state.uploadOther')
      }}</BaseButton>
    </BaseCard>

    <template v-else-if="wizard.analysis && wizard.decisions">
      <BaseCard id="section-files" class="analysis__files" tabindex="-1">
        <ProjectFileExplorer
          ref="explorer"
          :project-id="wizard.project.id"
          :project-name="wizard.project.name"
          :files="wizard.analysis.files"
          :excluded-files="wizard.decisions.excludedFiles"
          :disabled="wizard.isBusy"
          @toggle="wizard.toggleFile"
        />
      </BaseCard>

      <div class="analysis__grid">
        <div class="analysis__main">
          <SectionCard
            id="section-dependencies"
            :title="$t('analysis.deps.title')"
            :subtitle="$t('analysis.deps.subtitle', requirementsCount)"
            :questions="countIn('dependencies', 'question')"
          >
            <DependencyList
              :dependencies="wizard.analysis.dependencies"
              :packages="wizard.decisions.packages"
              :versions="wizard.decisions.versions"
              :excluded-modules="wizard.decisions.excludedModules"
              :disabled="wizard.isBusy"
              @set-package="wizard.setPackage"
              @set-version="wizard.setVersion"
              @toggle="wizard.toggleModule"
            />
          </SectionCard>

          <SectionCard
            id="section-run"
            :title="$t('analysis.run.title')"
            :questions="countIn('run', 'question')"
            :errors="countIn('run', 'blocker')"
          >
            <RunSettings
              v-model:run-spec="runSpec"
              v-model:python-version="pythonVersion"
              :entrypoints="wizard.analysis.entrypoints"
              :script-files="scriptFiles"
              :python-requires="wizard.analysis.pythonRequires"
              :candidates="wizard.analysis.launchCandidates"
              :disabled="wizard.isBusy"
            />
          </SectionCard>

          <SectionCard id="section-data" :title="$t('analysis.data.title')" :questions="countIn('data', 'question')">
            <DataConfirmation
              v-model:data-confirmed="dataConfirmed"
              v-model:input-description="inputDescription"
              v-model:expected-output="expectedOutput"
              v-model:description="description"
              :data-files="dataFiles"
              :disabled="wizard.isBusy"
            />
          </SectionCard>

          <SectionCard id="section-tools" :title="$t('workflow.toolsTitle')">
            <ProjectToolsPanel />
          </SectionCard>

          <SectionCard
            v-if="notes.length"
            id="section-notes"
            :title="$t('analysis.notes.title')"
            :subtitle="$t('analysis.notes.subtitle')"
          >
            <FindingList :findings="notes" />
          </SectionCard>
        </div>

        <ReadinessPanel
          class="analysis__aside"
          :project="wizard.project"
          :plan="visiblePlan"
          :save-state="wizard.saveState"
          :saved-at="wizard.savedAt"
          :is-building="wizard.isBuilding"
          :can-build="wizard.canBuild"
          :error="wizard.error && $t(wizard.error)"
          @focus="focusFinding"
          @build="wizard.build()"
          @reset="wizard.reset()"
          @reload="wizard.reloadDecisions()"
        />
      </div>
    </template>
  </div>
</template>

<style scoped>
.analysis {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.analysis__files {
  scroll-margin-top: 24px;
}

.analysis__files:focus {
  outline: none;
}

.analysis__grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 384px;
  gap: 24px;
  align-items: start;
}

.analysis__main {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.analysis__aside {
  position: sticky;
  top: 24px;
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

.analysis__skeleton {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: min(520px, 100%);
  margin-top: 16px;
}

.analysis__skeleton span {
  height: 14px;
  border-radius: var(--radius-pill);
  background: linear-gradient(90deg, #222 0%, #2c2c2c 50%, #222 100%);
  background-size: 200% 100%;
  animation: shimmer 1.4s ease-in-out infinite;
}

.analysis__skeleton span:nth-child(2n) {
  width: 70%;
}

@keyframes shimmer {
  from {
    background-position: 200% 0;
  }
  to {
    background-position: -200% 0;
  }
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
</style>
