<script setup lang="ts">
import { computed } from 'vue'
import { t } from '@/shared/i18n'
import { BaseIcon } from '@/shared/ui'
import type { AnalysisStatus } from '../model/types'

const props = defineProps<{ status: AnalysisStatus }>()

const label = computed(() => t(`project.analysis.${props.status}`))
</script>

<template>
  <span class="analysis" :class="`analysis--${status}`">
    <BaseIcon v-if="status === 'analyzing'" name="refresh" :size="20" spin class="analysis__spinner" />
    <span class="analysis__badge">{{ label }}</span>
  </span>
</template>

<style scoped>
.analysis {
  display: inline-flex;
  align-items: center;
  gap: 12px;
}

.analysis__badge {
  display: inline-flex;
  align-items: center;
  height: 28px;
  padding: 0 12px;
  border-radius: var(--radius-pill);
  font-size: 13px;
  font-weight: 500;
  white-space: nowrap;
}

.analysis--full .analysis__badge {
  background: var(--color-inverse-bg);
  color: var(--color-inverse-text);
}

.analysis--partial .analysis__badge {
  background: var(--color-bg);
  color: var(--color-text);
}

.analysis--analyzing .analysis__badge {
  background: var(--color-surface-raised);
  color: var(--color-text-muted);
  font-weight: 400;
}

.analysis--failed .analysis__badge {
  background: rgb(255 122 122 / 0.12);
  color: var(--color-danger);
}

.analysis__spinner {
  color: var(--color-text-secondary);
}
</style>
