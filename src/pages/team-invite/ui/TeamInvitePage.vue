<script setup lang="ts">
import { RouterLink } from 'vue-router'
import { RouteName } from '@/shared/config'
import { BaseIcon } from '@/shared/ui'
import { ActiveInvites, InviteInfoCard, InvitePanel } from '@/widgets/invite-panel'
import { TeamTabs } from '@/widgets/team-tabs'
</script>

<template>
  <div class="container team-invite">
    <h1 class="visually-hidden">{{ $t('routes.teamInvite') }}</h1>
    <TeamTabs />

    <div class="team-invite__grid">
      <InvitePanel />
      <InviteInfoCard />
    </div>

    <ActiveInvites class="team-invite__links" />

    <div class="team-invite__bottom">
      <RouterLink :to="{ name: RouteName.TeamProjects }" class="team-invite__back">
        {{ $t('invite.toProjects') }}
        <BaseIcon name="arrow-right" :size="18" />
      </RouterLink>
    </div>
  </div>
</template>

<style scoped>
.team-invite__grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 416px;
  gap: 24px;
  margin-top: 42px;
}

.team-invite__links {
  margin-top: 72px;
}

.team-invite__bottom {
  display: flex;
  justify-content: flex-end;
  margin-top: 64px;
  padding-bottom: 28px;
}

.team-invite__back {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 15px;
  font-weight: 500;
}

.team-invite__back:hover :deep(.icon) {
  transform: translateX(2px);
}

.team-invite__back :deep(.icon) {
  transition: transform var(--transition);
}

@media (max-width: 1024px) {
  .team-invite__grid {
    grid-template-columns: 1fr;
  }

  .team-invite__links {
  margin-top: 72px;
}

.team-invite__bottom {
    margin-top: 64px;
  }
}
</style>
