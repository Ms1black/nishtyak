<script setup lang="ts">
import { useTeamStore } from '@/entities/team'
import { useUploadWizardStore, UploadWizard } from '@/widgets/upload-wizard'
import { useProjectRoute } from '../model/useProjectRoute'
import { BaseButton, BaseCard } from '@/shared/ui'

const teams = useTeamStore()
const wizard = useUploadWizardStore()
const { isOpening, openError, retry, newProject } = useProjectRoute()
</script>

<template>
  <div class="container upload-page">
    <h1 class="visually-hidden">{{ $t('routes.upload') }}</h1>
    <BaseCard v-if="isOpening || openError" class="upload-page__lookup" :role="openError ? 'alert' : 'status'">
      <p>{{ $t(openError ?? 'upload.open.loading') }}</p>
      <div v-if="openError" class="upload-page__actions">
        <BaseButton variant="tonal" icon="refresh" @click="retry">{{ $t('common.retry') }}</BaseButton>
        <BaseButton variant="tonal" @click="newProject">{{ $t('analysis.panel.otherProject') }}</BaseButton>
      </div>
    </BaseCard>
    <p v-else-if="teams.currentTeam?.myRole === 'viewer' && !wizard.project" role="note">{{ $t('teams.readOnly') }}</p>
    <UploadWizard v-else />
  </div>
</template>

<style scoped>
.upload-page__lookup,
.upload-page__actions {
  display: flex;
  gap: 16px;
}
.upload-page__lookup {
  flex-direction: column;
}
</style>
