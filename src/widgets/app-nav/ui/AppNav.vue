<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { RouteName } from '@/shared/config'
import { BaseIcon, type IconName } from '@/shared/ui'

const route = useRoute()

const links: { label: string; icon: IconName; to: { name: RouteName }; match: string }[] = [
  { label: 'routes.upload', icon: 'upload', to: { name: RouteName.Upload }, match: '/upload' },
  { label: 'routes.teamProjects', icon: 'projects', to: { name: RouteName.TeamProjects }, match: '/team' },
]

const activeMatch = computed(() => links.find((l) => route.path.startsWith(l.match))?.match)
</script>

<template>
  <nav class="nav" :aria-label="$t('nav.label')">
    <div class="nav__inner">
      <RouterLink
        v-for="link in links"
        :key="link.match"
        :to="link.to"
        class="nav__link"
        :class="{ 'is-active': activeMatch === link.match }"
        :aria-current="activeMatch === link.match ? 'page' : undefined"
      >
        <BaseIcon :name="link.icon" :size="20" />
        {{ $t(link.label) }}
      </RouterLink>
    </div>
  </nav>
</template>

<style scoped>
.nav {
  background: var(--color-surface);
}

.nav__inner {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 64px;
  max-width: var(--container-max);
  margin-inline: auto;
  padding-inline: var(--header-px);
  overflow-x: auto;
  scrollbar-width: none;
}

.nav__link {
  display: inline-flex;
  align-items: center;
  gap: 12px;
  height: 44px;
  padding: 0 20px;
  border-radius: var(--radius-sm);
  color: var(--color-text-muted);
  font-size: 15px;
  white-space: nowrap;
  transition:
    background-color var(--transition),
    color var(--transition);
}

.nav__link:hover {
  color: var(--color-text);
}

.nav__link.is-active {
  background: var(--color-surface-raised);
  color: var(--color-text);
  font-weight: 500;
}
</style>
