<script setup lang="ts">
import { ref } from 'vue'
import { getRoleLabel, TeamLogo, useTeamStore } from '@/entities/team'
import { TeamCreateForm } from '@/features/team-create'
import { DeleteTeamButton } from '@/features/team-delete'
import { BaseCard } from '@/shared/ui'

const teamStore = useTeamStore()
const createForm = ref<InstanceType<typeof TeamCreateForm> | null>(null)

defineExpose({ focusCreate: () => createForm.value?.focus() })
</script>

<template>
  <BaseCard tag="section" padding="lg" class="teams" aria-labelledby="teams-title">
    <header class="teams__head">
      <h2 id="teams-title" class="teams__title">{{ $t('teams.title') }}</h2>
      <span class="teams__count">{{ teamStore.teams.length }}</span>
    </header>

    <TransitionGroup tag="ul" name="team" class="teams__list">
      <li v-for="team in teamStore.teams" :key="team.id" class="team">
        <TeamLogo :name="team.name" :src="team.avatarUrl" :size="44" class="team__logo" />
        <div class="team__info">
          <span class="team__name">{{ team.name }}</span>
          <span class="team__meta">
            {{ getRoleLabel(team.myRole) }} · {{ $t('units.members', team.membersCount) }}
          </span>
        </div>
        <span v-if="team.id === teamStore.currentTeamId" class="team__current">{{ $t('teams.current') }}</span>
        <button v-else type="button" class="team__switch" @click="teamStore.switchTeam(team.id)">{{ $t('teams.switch') }}</button>
        <div class="team__delete">
          <DeleteTeamButton v-if="team.myRole === 'owner'" :team="team" />
          <span v-else class="team__hint" :title="$t('teams.ownerOnly')">—</span>
        </div>
      </li>
    </TransitionGroup>

    <TeamCreateForm ref="createForm" class="teams__create" />
  </BaseCard>
</template>

<style scoped>
.teams__head {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.teams__title {
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 500;
}

.teams__count {
  color: var(--color-text-muted);
  font-size: 15px;
}

.teams__list {
  margin-top: 20px;
}

.team {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr) auto 80px;
  align-items: center;
  gap: 16px;
  padding: 14px 0;
  border-bottom: 1px solid var(--color-border);
}

.team__logo {
  background: var(--color-bg);
}

.team__info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.team__name {
  overflow: hidden;
  font-size: 16px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.team__meta {
  color: var(--color-text-muted);
  font-size: 13px;
}

.team__current {
  display: inline-flex;
  align-items: center;
  height: 26px;
  padding: 0 12px;
  border-radius: var(--radius-pill);
  background: var(--color-inverse-bg);
  color: var(--color-inverse-text);
  font-size: 12px;
  font-weight: 500;
}

.team__switch {
  color: var(--color-text-secondary);
  font-size: 14px;
  transition: color var(--transition);
}

.team__switch:hover {
  color: var(--color-text);
}

.team__delete {
  text-align: right;
}

.team__hint {
  color: var(--color-text-subtle);
}

.teams__create {
  margin-top: 28px;
}

.team-enter-active,
.team-leave-active {
  transition:
    opacity 200ms ease,
    transform 200ms ease;
}

.team-enter-from,
.team-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
