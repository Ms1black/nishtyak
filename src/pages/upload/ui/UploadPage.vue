<script setup lang="ts">
import { useTeamStore } from '@/entities/team'
import { useUploadWizardStore, UploadWizard } from '@/widgets/upload-wizard'
import { useProjectRoute } from '../model/useProjectRoute'

const teams = useTeamStore()
const wizard = useUploadWizardStore()
useProjectRoute()
</script>

<template>
  <div class="container upload-page">
    <h1 class="visually-hidden">{{ $t('routes.upload') }}</h1>
    <p v-if="teams.currentTeam?.myRole === 'viewer' && !wizard.project" role="note">{{ $t('teams.readOnly') }}</p>
    <UploadWizard v-else />
  </div>
</template>
