<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore, type Project } from '@/entities/project'
import { useTeamStore } from '@/entities/team'
import { filterByName, ProjectSearch } from '@/features/project-search'
import { ProjectSort, sortByDate, type DateSortOrder } from '@/features/project-sort'
import { RouteName } from '@/shared/config'
import { t } from '@/shared/i18n'
import { isLocalWorkspace } from '@/shared/api'
import { BaseButton } from '@/shared/ui'
import { ProjectsTable } from '@/widgets/projects-table'
import { TeamTabs } from '@/widgets/team-tabs'

const router = useRouter()
const teamStore = useTeamStore()
const projectStore = useProjectStore()

const query = ref('')
const order = ref<DateSortOrder>('newest')

const visibleProjects = computed(() => sortByDate(filterByName(projectStore.items, query.value), order.value))

const summary = computed(() => {
  const total = projectStore.items.length
  const shown = visibleProjects.value.length
  if (!total) return ''
  if (shown === total) return total === 1 ? t('projects.shownOne') : t('projects.shownAll', total)
  return t('projects.found', { shown, total: t('units.projects', total) })
})

function openProject(project: Project) {
  router.push({ name: RouteName.Upload, query: { project: project.id } })
}

watch(
  () => teamStore.currentTeamId,
  (teamId) => {
    if (teamId) projectStore.fetchAll(teamId)
  },
  { immediate: true },
)
</script>

<template>
  <div class="container team-projects">
    <h1 class="visually-hidden">{{ $t('routes.teamProjects') }}</h1>
    <h2 v-if="isLocalWorkspace">{{ $t('integration.projects') }}</h2>
    <TeamTabs v-else />

    <div class="team-projects__toolbar">
      <ProjectSearch v-model="query" />
      <ProjectSort v-model="order" />
    </div>

    <div v-if="projectStore.status === 'error'" class="team-projects__error">
      <p>{{ $t('projects.loadError') }}</p>
      <BaseButton variant="tonal" size="sm" icon="refresh" @click="teamStore.currentTeamId && projectStore.fetchAll(teamStore.currentTeamId)">
        {{ $t('common.retry') }}
      </BaseButton>
    </div>

    <ProjectsTable
      v-else
      :projects="visibleProjects"
      :loading="projectStore.status === 'loading'"
      :empty-text="query ? $t('projects.notFound') : $t('projects.empty')"
      @open="openProject"
    />

    <p class="team-projects__summary">{{ summary }}</p>
  </div>
</template>

<style scoped>
.team-projects__toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin: 42px 0 20px;
}

.team-projects__summary {
  margin-top: 20px;
  color: var(--color-text-muted);
  font-size: 13px;
}

.team-projects__error {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  padding: 48px;
  border-radius: var(--radius-xl);
  background: var(--color-surface);
  color: var(--color-text-secondary);
}
</style>
