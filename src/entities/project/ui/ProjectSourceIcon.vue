<script setup lang="ts">
import { BaseIcon } from '@/shared/ui'
import type { ProjectSourceType } from '../model/types'

defineProps<{ sourceType: ProjectSourceType }>()
</script>

<template>
  <span class="source-icon">
    <BaseIcon :name="sourceType === 'zip' ? 'folder-code' : 'file-code'" :size="22" />
  </span>
</template>

<style scoped>
.source-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  border-radius: var(--radius-sm);
  background: var(--color-bg);
  color: var(--color-text);
}
</style>
