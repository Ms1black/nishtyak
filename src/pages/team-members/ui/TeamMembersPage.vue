<script setup lang="ts">
import { MembersPanel } from '@/widgets/members-panel'
import { TeamTabs } from '@/widgets/team-tabs'
</script>

<template>
  <div class="container">
    <h1 class="visually-hidden">{{ $t('routes.teamMembers') }}</h1>
    <TeamTabs />
    <MembersPanel class="team-members__panel" />
  </div>
</template>

<style scoped>
.team-members__panel {
  margin-top: 42px;
}
</style>
