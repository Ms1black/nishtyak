<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { TeamLogo, useTeamStore } from '@/entities/team'
import { useClickOutside } from '@/shared/lib'
import { RouteName } from '@/shared/config'
import { BaseIcon } from '@/shared/ui'

const teamStore = useTeamStore()
const router = useRouter()
const root = ref<HTMLElement | null>(null)
const isOpen = ref(false)

useClickOutside(root, () => (isOpen.value = false))

function createTeam() {
  isOpen.value = false
  router.push({ name: RouteName.Profile, query: { createTeam: '1' } })
}

function select(id: string) {
  teamStore.switchTeam(id)
  isOpen.value = false
}
</script>

<template>
  <div ref="root" class="workspace" @keydown.esc="isOpen = false">
    <button
      type="button"
      class="workspace__trigger"
      aria-haspopup="menu"
      :aria-expanded="isOpen"
      @click="isOpen = !isOpen"
    >
      <TeamLogo :name="teamStore.currentTeam?.name ?? ''" :src="teamStore.currentTeam?.avatarUrl" />
      <span class="workspace__text">
        <span class="workspace__label">{{ $t('header.workspace') }}</span>
        <span class="workspace__name">{{ teamStore.currentTeam?.name ?? '…' }}</span>
      </span>
      <BaseIcon name="chevron-down" :size="18" class="workspace__chevron" :class="{ 'is-open': isOpen }" />
    </button>

    <Transition name="pop">
      <ul v-if="isOpen" class="workspace__menu" role="menu">
        <li v-for="team in teamStore.teams" :key="team.id" role="none">
          <button
            type="button"
            role="menuitemradio"
            :aria-checked="team.id === teamStore.currentTeamId"
            class="workspace__option"
            @click="select(team.id)"
          >
            <TeamLogo :name="team.name" :src="team.avatarUrl" />
            <span class="workspace__option-name">{{ team.name }}</span>
            <BaseIcon v-if="team.id === teamStore.currentTeamId" name="check" :size="18" />
          </button>
        </li>
        <li role="none" class="workspace__divider" />
        <li role="none">
          <button type="button" role="menuitem" class="workspace__option workspace__option--create" @click="createTeam">
            <span class="workspace__plus"><BaseIcon name="plus" :size="16" /></span>
            <span class="workspace__option-name">{{ $t('header.createTeam') }}</span>
          </button>
        </li>
      </ul>
    </Transition>
  </div>
</template>

<style scoped>
.workspace {
  position: relative;
}

.workspace__trigger {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 4px 8px 4px 0;
  border-radius: var(--radius-md);
  text-align: left;
}

.workspace__text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.workspace__label {
  color: var(--color-text-secondary);
  font-size: 11px;
  line-height: 1.2;
}

.workspace__name {
  font-family: var(--font-display);
  font-size: 16px;
  font-weight: 600;
  line-height: 1.2;
}

.workspace__chevron {
  margin-left: 8px;
  color: var(--color-text-secondary);
  transition: transform var(--transition);
}

.workspace__chevron.is-open {
  transform: rotate(180deg);
}

.workspace__menu {
  position: absolute;
  z-index: 30;
  top: calc(100% + 10px);
  left: 0;
  min-width: 260px;
  padding: 6px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-raised);
  box-shadow: 0 16px 40px rgb(0 0 0 / 0.5);
}

.workspace__option {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 8px;
  border-radius: var(--radius-sm);
  text-align: left;
}

.workspace__option:hover {
  background: #303030;
}

.workspace__option :deep(.team-logo) {
  width: 32px;
  height: 32px;
  font-size: 14px;
  background: var(--color-bg);
}

.workspace__divider {
  height: 1px;
  margin: 6px 0;
  background: var(--color-border-strong);
}

.workspace__plus {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: 1px dashed var(--color-border-strong);
  border-radius: var(--radius-sm);
  color: var(--color-text-secondary);
}

.workspace__option--create {
  color: var(--color-text-secondary);
}

.workspace__option-name {
  flex: 1;
}

.pop-enter-active,
.pop-leave-active {
  transition:
    opacity var(--transition),
    transform var(--transition);
}

.pop-enter-from,
.pop-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

@media (max-width: 640px) {
  .workspace__label {
    display: none;
  }
}
</style>
