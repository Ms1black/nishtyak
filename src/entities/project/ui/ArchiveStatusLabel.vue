<script setup lang="ts">
import { formatBytes } from '@/shared/lib'
import { BaseIcon } from '@/shared/ui'
import type { ProjectArchive } from '../model/types'

defineProps<{ archive: ProjectArchive }>()
</script>

<template>
  <div v-if="archive.status === 'ready'" class="archive archive--ready">
    <span class="archive__title">
      <BaseIcon name="archive" :size="20" />
      {{ $t('project.archive.ready') }}
    </span>
    <span v-if="archive.sizeBytes" class="archive__size">{{ formatBytes(archive.sizeBytes) }}</span>
  </div>
  <span v-else-if="archive.status === 'building'" class="archive archive--muted archive__title">
    <BaseIcon name="refresh" :size="18" spin />
    {{ $t('project.archive.building') }}
  </span>
  <span v-else class="archive archive--muted">
    {{ archive.status === 'not_built' ? $t('project.archive.notBuilt') : $t('project.archive.unavailable') }}
  </span>
</template>

<style scoped>
.archive {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 16px;
}

.archive__title {
  display: inline-flex;
  flex-direction: row;
  align-items: center;
  gap: 10px;
}

.archive--muted {
  color: var(--color-text-secondary);
}

.archive__size {
  padding-left: 0;
  color: var(--color-text-subtle);
  font-size: 12px;
}
</style>
