<script setup lang="ts">
import {
  AnalysisBadge,
  ArchiveStatusLabel,
  getProjectAction,
  getProjectMeta,
  ProjectSourceIcon,
  type Project,
} from '@/entities/project'
import { useArchiveActions } from '@/features/archive-actions'
import { t } from '@/shared/i18n'
import { formatRelativeDate } from '@/shared/lib'
import { BaseIcon } from '@/shared/ui'

defineProps<{
  projects: Project[]
  loading?: boolean
  emptyText?: string
}>()

const emit = defineEmits<{
  open: [project: Project]
}>()

const archive = useArchiveActions()

function runAction(project: Project) {
  const action = getProjectAction(project)
  switch (action.kind) {
    case 'download':
      return archive.download(project)
    case 'build':
      return archive.build(project)
    default:
      emit('open', project)
  }
}

function actionLabel(project: Project): string {
  if (project.archive.status === 'building') return t('project.archive.building')
  return t(getProjectAction(project).label)
}
</script>

<template>
  <div class="table-scroll">
    <div class="table" role="table" :aria-label="$t('projects.tableLabel')">
      <div class="table__row table__row--head" role="row">
        <span role="columnheader">{{ $t('projects.columns.project') }}</span>
        <span role="columnheader">{{ $t('projects.columns.analysis') }}</span>
        <span role="columnheader">{{ $t('projects.columns.archive') }}</span>
        <span role="columnheader">{{ $t('projects.columns.updated') }}</span>
        <span role="columnheader">{{ $t('projects.columns.link') }}</span>
      </div>

      <template v-if="loading && !projects.length">
        <div v-for="n in 4" :key="n" class="table__row table__row--skeleton" role="row" aria-hidden="true">
          <span class="skeleton skeleton--project" />
          <span class="skeleton skeleton--badge" />
          <span class="skeleton skeleton--text" />
          <span class="skeleton skeleton--text" />
          <span class="skeleton skeleton--text" />
        </div>
      </template>

      <div v-else-if="!projects.length" class="table__empty" role="row">
        <span role="cell">{{ emptyText ?? $t('projects.empty') }}</span>
      </div>

      <div
        v-for="project in projects"
        v-else
        :key="project.id"
        class="table__row table__row--body"
        role="row"
        @click="runAction(project)"
      >
        <div class="cell-project" role="cell">
          <ProjectSourceIcon :source-type="project.sourceType" />
          <div class="cell-project__text">
            <span class="cell-project__name">{{ project.name }}</span>
            <span class="cell-project__meta">
              <span v-for="item in getProjectMeta(project)" :key="item">{{ item }}</span>
            </span>
          </div>
        </div>
        <div role="cell">
          <AnalysisBadge :status="project.analysis" />
        </div>
        <div role="cell">
          <ArchiveStatusLabel :archive="project.archive" />
        </div>
        <div class="cell-date" role="cell">
          <time :datetime="project.updatedAt">{{ formatRelativeDate(project.updatedAt) }}</time>
        </div>
        <div role="cell">
          <button
            type="button"
            class="cell-action"
            :disabled="archive.isPending(project.id)"
            :aria-label="`${actionLabel(project)}: ${project.name}`"
            @click.stop="runAction(project)"
          >
            <span>{{ actionLabel(project) }}</span>
            <BaseIcon v-if="archive.isPending(project.id)" name="refresh" :size="18" spin />
            <BaseIcon v-else name="chevron-right" :size="18" class="cell-action__chevron" />
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.table-scroll {
  overflow-x: auto;
  border-radius: var(--radius-xl);
}

.table {
  min-width: 960px;
  overflow: hidden;
  border-radius: var(--radius-xl);
  background: var(--color-surface);
}

.table__row {
  display: grid;
  grid-template-columns: 1.55fr 1.6fr 1.35fr 1fr 1fr;
  align-items: center;
  gap: 16px;
  padding: 0 20px;
}

.table__row--head {
  height: 46px;
  background: var(--color-surface-header);
  color: var(--color-text-secondary);
  font-size: 13px;
}

.table__row--body,
.table__row--skeleton {
  min-height: 108px;
}

.table__row--body {
  cursor: pointer;
  transition: background-color var(--transition);
}

.table__row--body:hover {
  background: var(--color-surface-hover);
}

.table__row--body:hover .cell-action__chevron {
  color: var(--color-text);
  transform: translateX(2px);
}

.cell-project {
  display: flex;
  align-items: center;
  gap: 14px;
  min-width: 0;
}

.cell-project__text {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.cell-project__name {
  overflow: hidden;
  font-family: var(--font-display);
  font-size: 17px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cell-project__meta {
  display: flex;
  gap: 10px;
  color: var(--color-text-muted);
  font-size: 13px;
  white-space: nowrap;
}

.cell-date {
  color: var(--color-text-subtle);
  font-size: 13px;
}

.cell-action {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  padding: 8px 0;
  font-size: 15px;
  font-weight: 500;
  text-align: left;
}

.cell-action:disabled {
  color: var(--color-text-secondary);
  cursor: progress;
}

.cell-action__chevron {
  color: var(--color-text-secondary);
  transition:
    color var(--transition),
    transform var(--transition);
}

.table__empty {
  padding: 48px 20px;
  color: var(--color-text-muted);
  text-align: center;
}

.skeleton {
  display: block;
  height: 14px;
  border-radius: var(--radius-pill);
  background: linear-gradient(90deg, #222 0%, #2c2c2c 50%, #222 100%);
  background-size: 200% 100%;
  animation: shimmer 1.4s ease-in-out infinite;
}

.skeleton--project {
  width: 70%;
  height: 40px;
  border-radius: var(--radius-sm);
}

.skeleton--badge {
  width: 90px;
  height: 28px;
}

.skeleton--text {
  width: 60%;
}

@keyframes shimmer {
  from {
    background-position: 200% 0;
  }
  to {
    background-position: -200% 0;
  }
}
</style>
