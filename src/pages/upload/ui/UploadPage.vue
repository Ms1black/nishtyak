<script setup lang="ts">
import { onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useProjectStore } from '@/entities/project'
import { useTeamStore } from '@/entities/team'
import { UploadWizard, useUploadWizardStore } from '@/widgets/upload-wizard'

const route = useRoute()
const router = useRouter()
const wizard = useUploadWizardStore()
const projectStore = useProjectStore()
const teamStore = useTeamStore()

onMounted(async () => {
  const projectId = typeof route.query.project === 'string' ? route.query.project : null
  if (!projectId) return

  await teamStore.init()
  if (!projectStore.getById(projectId) && teamStore.currentTeamId) {
    await projectStore.fetchAll(teamStore.currentTeamId)
  }
  const project = projectStore.getById(projectId)
  if (project) await wizard.openProject(project)
  router.replace({ query: {} })
})
</script>

<template>
  <div class="container upload-page">
    <h1 class="visually-hidden">{{ $t('routes.upload') }}</h1>
    <UploadWizard />
  </div>
</template>

