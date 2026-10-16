<script setup lang="ts">
import { computed } from 'vue'
import {
  AnalysisBadge,
  describeFinding,
  getFindingKind,
  getProjectMeta,
  ProjectSourceIcon,
  type Finding,
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
  canBuild: boolean
  error: string | null
}>()

const emit = defineEmits<{ focus: [finding: Finding]; build: []; reset: []; reload: [] }>()

const byKind = (kind: ReturnType<typeof getFindingKind>) =>
  computed(() => props.plan?.findings.filter((f) => getFindingKind(f) === kind) ?? [])

const blockers = byKind('blocker')
const questions = byKind('question')
const limitations = byKind('limitation')

const state = computed(() =>
  props.plan
    ? { title: t(`analysis.plan.${props.plan.state}.title`), text: t(`analysis.plan.${props.plan.state}.text`) }
    : null,
)
const openCount = computed(() => blockers.value.length + questions.value.length)

const saveText = computed(() => {
  switch (props.saveState) {
    case 'saving':
      return t('analysis.panel.saving')
    case 'saved':
      return props.savedAt ? t('analysis.panel.savedAt', { time: formatTime(props.savedAt) }) : t('common.saved')
    case 'error':
      return t('analysis.panel.saveError')
    case 'conflict':
      return t('analysis.panel.conflict')
    default:
      return t('analysis.panel.autosave')
  }
})

const buildLabel = computed(() =>
  props.plan?.state === 'draft' ? t('analysis.panel.buildDraft') : t('analysis.panel.build'),
)
</script>

<template>
  <aside class="panel" :aria-label="$t('analysis.panel.label')">
    <div class="panel__project">
      <ProjectSourceIcon :source-type="project.sourceType" />
      <div class="panel__project-text">
        <span class="panel__name">{{ project.name }}</span>
        <span class="panel__meta">{{ getProjectMeta(project).join(' · ') }}</span>
      </div>
      <AnalysisBadge :status="project.analysis" />
    </div>

    <div v-if="plan && state" class="panel__state" :class="`panel__state--${plan.state}`">
      <span class="panel__state-icon">
        <BaseIcon
          :name="plan.state === 'blocked' ? 'alert' : plan.state === 'draft' ? 'circle-dashed' : 'check'"
          :size="22"
          :stroke-width="plan.state === 'ready_to_verify' ? 2.6 : 2"
        />
      </span>
      <div>
        <p class="panel__state-title">{{ state.title }}</p>
        <p class="panel__state-text">{{ state.text }}</p>
      </div>
    </div>

    <template v-if="openCount">
      <p class="panel__group">
        {{ blockers.length ? $t('analysis.panel.leftFix') : $t('analysis.panel.leftAnswer') }} ·
        {{ $t('units.items', openCount) }}
      </p>
      <ul class="panel__list">
        <li v-for="f in [...blockers, ...questions]" :key="f.id">
          <button type="button" class="item" :class="`item--${getFindingKind(f)}`" @click="emit('focus', f)">
            <BaseIcon :name="getFindingKind(f) === 'blocker' ? 'alert' : 'circle-dashed'" :size="18" class="item__icon" />
            <span class="item__text">{{ describeFinding(f) }}</span>
            <BaseIcon name="chevron-right" :size="16" class="item__chevron" />
          </button>
        </li>
      </ul>
    </template>

    <template v-if="limitations.length">
      <p class="panel__group">{{ $t('analysis.panel.limitations') }}</p>
      <ul class="panel__list">
        <li v-for="f in limitations" :key="f.id">
          <button type="button" class="item item--limitation" @click="emit('focus', f)">
            <BaseIcon name="info" :size="18" class="item__icon" />
            <span class="item__text">{{ describeFinding(f) }}</span>
          </button>
        </li>
      </ul>
    </template>

    <div class="panel__footer">
      <p class="panel__save" :class="`panel__save--${saveState}`" aria-live="polite">
        {{ saveText }}
        <button v-if="saveState === 'conflict'" type="button" class="panel__link" @click="emit('reload')">
          {{ $t('analysis.panel.reload') }}
        </button>
      </p>
      <p v-if="error" class="panel__error" role="alert">{{ error }}</p>
      <BaseButton block :disabled="!canBuild" :loading="isBuilding" @click="emit('build')">
        {{ buildLabel }}
      </BaseButton>
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

.panel__group {
  margin-bottom: -10px;
  color: var(--color-text-muted);
  font-size: 12px;
}

.panel__list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  width: 100%;
  padding: 10px;
  margin: 0 -10px;
  width: calc(100% + 20px);
  border-radius: var(--radius-sm);
  text-align: left;
  transition: background-color var(--transition);
}

.item:hover {
  background: var(--color-surface-hover);
}

.item__icon {
  margin-top: 1px;
  color: var(--color-text-secondary);
}

.item--blocker .item__icon {
  color: #ff5c5c;
}

.item--limitation .item__icon {
  color: var(--color-text-subtle);
}

.item__text {
  flex: 1;
  font-size: 13px;
  line-height: 1.45;
}

.item--limitation .item__text {
  color: var(--color-text-muted);
}

.item__chevron {
  margin-top: 1px;
  color: var(--color-text-subtle);
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
