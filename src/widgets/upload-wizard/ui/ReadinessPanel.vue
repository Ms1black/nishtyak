<script setup lang="ts">
import { computed } from 'vue'
import {
  AnalysisBadge,
  getProjectMeta,
  isPackageBlocked,
  ProjectSourceIcon,
  type PackagePlan,
  type Project,
} from '@/entities/project'
import { t } from '@/shared/i18n'
import { formatTime } from '@/shared/lib'
import { BaseButton, BaseIcon } from '@/shared/ui'
import type { SaveState } from '../model/uploadWizardStore'

const props = defineProps<{
  project: Project
  plan: PackagePlan | null
  saveState: SaveState
  savedAt: Date | null
  isBuilding: boolean
  isPreparingEnvironment?: boolean
  isProcessing?: boolean
  progressText?: string | null
  isRefreshing: boolean
  canBuild: boolean
  error: string | null
}>()

const emit = defineEmits<{ build: []; reset: []; reload: []; refresh: [] }>()
const isBlocked = computed(() => isPackageBlocked(props.plan))
const needsInput = computed(() => !isBlocked.value && props.plan?.automation?.phase === 'needs_input')
const isWorking = computed(() =>
  props.isBuilding || props.isProcessing || props.isPreparingEnvironment || props.isRefreshing,
)
const planState = computed(() =>
  isBlocked.value ? 'blocked' : props.plan?.automation?.phase === 'draft' ? 'draft' : props.plan?.state,
)
const stateTone = computed(() =>
  isBlocked.value ? 'blocked' : props.isRefreshing ? 'processing' : needsInput.value ? 'needs_input' : isWorking.value ? 'processing' : planState.value,
)
const stateIcon = computed(() => {
  if (isBlocked.value) return 'alert'
  if (isWorking.value) return 'refresh'
  if (needsInput.value || planState.value === 'blocked') return 'alert'
  return planState.value === 'draft' ? 'circle-dashed' : 'check'
})

const state = computed(() => {
  if (isBlocked.value) return { title: t('analysis.plan.blocked.title'), text: t('analysis.plan.blocked.text') }
  if (props.isRefreshing) {
    return { title: t('analysis.automation.validatingAnswer'), text: t('analysis.automation.phases.validating') }
  }
  if (needsInput.value) {
    return { title: t('analysis.automation.awaitingAnswer'), text: t('analysis.automation.answerNext') }
  }
  if (props.isProcessing && props.progressText) {
    return { title: t('analysis.automation.preparing'), text: props.progressText }
  }
  if (props.isPreparingEnvironment) {
    return { title: t('analysis.automatic.preparingTitle'), text: t('analysis.automatic.resolvingHint') }
  }
  if (props.isBuilding) {
    return { title: t('analysis.automatic.buildingTitle'), text: t('analysis.automatic.buildingHint') }
  }
  if (!props.plan) return null
  return {
    title: t(`analysis.plan.${planState.value}.title`),
    text: t(`analysis.plan.${planState.value}.text`),
  }
})

const buttonText = computed(() => {
  if (props.isRefreshing) return t('analysis.automation.validatingAnswer')
  if (needsInput.value) return t('analysis.automation.awaitingAnswer')
  if (props.isProcessing) return t('analysis.automation.preparing')
  if (props.isPreparingEnvironment) return t('analysis.automatic.preparingTitle')
  if (props.isBuilding) return t('analysis.automatic.buildingTitle')
  return t('analysis.panel.build')
})

const saveText = computed(() => {
  switch (props.saveState) {
    case 'saving':
      return t('analysis.panel.saving')
    case 'saved':
      return props.savedAt
        ? t('analysis.panel.savedAt', { time: formatTime(props.savedAt) })
        : t('common.saved')
    case 'error':
      return t('analysis.panel.saveError')
    case 'conflict':
      return t('analysis.panel.conflict')
    default:
      return t('analysis.panel.autosave')
  }
})
</script>

<template>
  <aside class="panel" :aria-label="$t('analysis.panel.label')">
    <div class="panel__project">
      <ProjectSourceIcon :source-type="project.sourceType" />
      <div class="panel__project-text">
        <span class="panel__name">{{ project.name }}</span>
        <span class="panel__meta">{{ getProjectMeta(project).join(' · ') }}</span>
      </div>
      <span v-if="needsInput && !isWorking" class="panel__badge">{{ $t('analysis.automation.needsAnswer') }}</span>
      <span v-else-if="isWorking" class="panel__badge">{{ $t('analysis.automation.preparing') }}</span>
      <AnalysisBadge v-else-if="!plan?.automation" :status="project.analysis" />
    </div>

    <div v-if="plan && state" class="panel__state" :class="`panel__state--${stateTone}`">
      <span class="panel__state-icon">
        <BaseIcon
          :name="stateIcon"
          :spin="isWorking"
          :size="22"
          :stroke-width="plan.state === 'ready_to_verify' ? 2.6 : 2"
        />
      </span>
      <div>
        <p class="panel__state-title">{{ state.title }}</p>
        <p class="panel__state-text">{{ state.text }}</p>
      </div>
    </div>

    <div class="panel__footer">
      <p v-if="error" class="panel__error" role="alert">{{ error }}</p>
      <BaseButton
        v-if="saveState === 'error' && !needsInput"
        block
        variant="tonal"
        :loading="isRefreshing"
        :disabled="isBuilding"
        @click="emit('refresh')"
      >
        {{ $t('analysis.panel.refresh') }}
      </BaseButton>
      <BaseButton block :disabled="!canBuild || isBlocked" :loading="isWorking" @click="emit('build')">
        {{ buttonText }}
      </BaseButton>
      <p v-if="saveState !== 'idle'" class="panel__save" :class="`panel__save--${saveState}`" aria-live="polite">
        {{ saveText }}
        <button v-if="saveState === 'conflict'" type="button" class="panel__link" @click="emit('reload')">
          {{ $t('analysis.panel.reload') }}
        </button>
      </p>
      <BaseButton block variant="tonal" :disabled="isBuilding" @click="emit('reset')">
        {{ $t('analysis.panel.otherProject') }}
      </BaseButton>
    </div>
  </aside>
</template>

<style scoped>
.panel {
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 28px;
  border-radius: var(--radius-xl);
  background: var(--color-surface);
}

.panel__project {
  display: flex;
  align-items: center;
  gap: 12px;
}
.panel__badge {
  color: var(--color-text-muted);
  font-size: 12px;
}

.panel__project-text {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
}

.panel__name {
  overflow: hidden;
  font-family: var(--font-display);
  font-size: 17px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.panel__meta {
  color: var(--color-text-muted);
  font-size: 12px;
}

.panel__state {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 16px;
  border-radius: var(--radius-lg);
  background: var(--color-bg);
}

.panel__state-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: var(--color-surface-raised);
}

.panel__state--blocked .panel__state-icon {
  background: rgb(255 77 77 / 0.14);
  color: #ff5c5c;
}
.panel__state--needs_input .panel__state-icon {
  background: rgb(230 160 59 / 0.14);
  color: #e6a03b;
}

.panel__state--ready_to_verify .panel__state-icon {
  background: var(--color-inverse-bg);
  color: var(--color-inverse-text);
}

.panel__state-title {
  font-family: var(--font-display);
  font-size: 18px;
  font-weight: 500;
}

.panel__state-text {
  margin-top: 2px;
  color: var(--color-text-muted);
  font-size: 13px;
  line-height: 1.4;
}

.panel__footer {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-top: 20px;
  border-top: 1px solid var(--color-border);
}

.panel__save {
  margin-bottom: 4px;
  color: var(--color-text-subtle);
  font-size: 12px;
}

.panel__save--error,
.panel__save--conflict,
.panel__error {
  color: var(--color-danger);
  font-size: 12px;
}

.panel__link {
  margin-left: 6px;
  color: var(--color-text);
  text-decoration: underline;
  text-underline-offset: 3px;
}
</style>
