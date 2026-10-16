<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { RouteName } from '@/shared/config'
import { t } from '@/shared/i18n'
import { SegmentedControl, type SegmentedItem } from '@/shared/ui'

const route = useRoute()

const items = computed<SegmentedItem<string>[]>(() => [
  { value: RouteName.TeamProjects, label: t('routes.teamProjects'), to: { name: RouteName.TeamProjects } },
  { value: RouteName.TeamMembers, label: t('routes.teamMembers'), to: { name: RouteName.TeamMembers } },
  { value: RouteName.TeamInvite, label: t('routes.teamInvite'), to: { name: RouteName.TeamInvite } },
])

const active = computed(() => String(route.name ?? ''))
</script>

<template>
  <SegmentedControl :items="items" :model-value="active" :aria-label="$t('nav.teamSection')" />
</template>
