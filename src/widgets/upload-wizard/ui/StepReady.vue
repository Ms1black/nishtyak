<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { ArchiveTree, getArchiveFileName } from '@/entities/project'
import { useArchiveActions } from '@/features/archive-actions'
import { RouteName } from '@/shared/config'
import { t } from '@/shared/i18n'
import { formatBytes } from '@/shared/lib'
import { BaseButton, BaseCard, BaseIcon } from '@/shared/ui'
import { useUploadWizardStore } from '../model/uploadWizardStore'

const wizard = useUploadWizardStore()
const router = useRouter()
const archive = useArchiveActions()

const isDraft = computed(() => wizard.plan?.state === 'draft')

const treeItems = computed(() => {
  if (wizard.project?.archive.files) return wizard.project.archive.files
  return []
})

const summary = computed(() => [
  { label: t('ready.summary.packages'), value: String(wizard.plan?.requirements.length ?? 0) },
  { label: t('ready.summary.run'), value: wizard.plan?.runSpec?.target ?? wizard.decisions?.runSpec?.target ?? '—' },
  { label: t('ready.summary.python'), value: wizard.decisions?.pythonVersion ?? t('ready.pythonUnknown') },
  { label: t('ready.summary.excluded'), value: String(wizard.decisions?.excludedFiles.length ?? 0) },
  {
    label: t('ready.summary.size'),
    value: wizard.project?.archive.sizeBytes ? formatBytes(wizard.project.archive.sizeBytes) : '—',
  },
])

function download() {
  if (wizard.project) archive.download(wizard.project)
}

function toProjects() {
  wizard.reset()
  router.push({ name: RouteName.TeamProjects })
}
</script>

<template>
  <div v-if="wizard.project" class="step-grid">
    <BaseCard padding="lg" class="ready">
      <div class="ready__status">
        <span class="ready__check"><BaseIcon name="check" :size="22" :stroke-width="2.5" /></span>
        <div>
          <h2 class="step-title">{{ isDraft ? $t('ready.titleDraft') : $t('ready.title') }}</h2>
          <p class="step-muted">
            {{ isDraft ? $t('ready.textDraft') : $t('ready.text', { name: wizard.project.name }) }}
          </p>
        </div>
      </div>
      <ArchiveTree :root-name="getArchiveFileName(wizard.project)" :items="treeItems" />
    </BaseCard>

    <BaseCard tag="aside" class="summary">
      <h2 class="step-title">{{ $t('ready.summaryTitle') }}</h2>
      <dl class="summary__list">
        <div v-for="row in summary" :key="row.label" class="summary__row">
          <dt>{{ row.label }}</dt>
          <dd>{{ row.value }}</dd>
        </div>
      </dl>
    </BaseCard>

    <div class="step-actions">
      <p v-if="archive.error.value" role="alert" class="step-actions__hint is-error">{{ archive.error.value }}</p>
      <BaseButton variant="tonal" icon="arrow-left" @click="wizard.backToAnalysis()">{{ $t('ready.edit') }}</BaseButton>
      <span class="step-actions__spacer" />
      <BaseButton variant="tonal" @click="toProjects">{{ $t('ready.toProjects') }}</BaseButton>
      <BaseButton icon="download" :loading="archive.isPending(wizard.project.id)" @click="download">
        {{ $t('ready.download') }}
      </BaseButton>
    </div>
  </div>
</template>

<style scoped src="./step.css"></style>
<style scoped>
.ready {
  display: flex;
  flex-direction: column;
  gap: 28px;
}

.ready__status {
  display: flex;
  align-items: center;
  gap: 16px;
}

.ready__status .step-muted {
  margin-top: 4px;
  font-size: 14px;
}

.ready__check {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: var(--color-inverse-bg);
  color: var(--color-inverse-text);
}

.summary {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.summary__list {
  display: flex;
  flex-direction: column;
  margin: 0;
}

.summary__row {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 0;
  border-bottom: 1px solid var(--color-border);
  font-size: 14px;
}

.summary__row:last-child {
  border-bottom: 0;
}

.summary__row dt {
  color: var(--color-text-muted);
}

.summary__row dd {
  margin: 0;
  overflow: hidden;
  font-weight: 500;
  text-align: right;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
