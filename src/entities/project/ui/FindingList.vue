<script setup lang="ts">
import { computed } from 'vue'
import { BaseIcon, type IconName } from '@/shared/ui'
import { summarizeFindings } from '../lib/findingSummary'
import type { Finding, FindingSeverity } from '../model/types'

const props = defineProps<{ findings: Finding[]; residualOnly?: boolean }>()
const items = computed(() => summarizeFindings(props.findings, props.residualOnly))

const ICON: Record<FindingSeverity, IconName> = { error: 'alert', warning: 'warning', info: 'info' }
</script>

<template>
  <ul class="findings">
    <li v-for="f in items" :key="f.id" class="finding" :class="`finding--${f.severity}`">
      <BaseIcon :name="ICON[f.severity]" :size="18" class="finding__icon" />
      <p class="finding__text">{{ f.text }}</p>
    </li>
  </ul>
</template>

<style scoped>
.findings {
  display: flex;
  flex-direction: column;
}

.finding {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px 0;
  border-bottom: 1px solid var(--color-border);
}

.finding:last-child {
  border-bottom: 0;
}

.finding__icon {
  margin-top: 1px;
  color: var(--color-text-muted);
}

.finding--warning .finding__icon {
  color: var(--color-text-secondary);
}

.finding--error .finding__icon {
  color: var(--color-danger);
}

.finding__text {
  color: var(--color-text-secondary);
  font-size: 14px;
  line-height: 1.45;
}
</style>
