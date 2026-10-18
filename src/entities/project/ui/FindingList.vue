<script setup lang="ts">
import { computed } from 'vue'
import { BaseIcon, type IconName } from '@/shared/ui'
import { summarizeFindings } from '../lib/findingSummary'
import type { Finding, FindingSeverity } from '../model/types'

const props = defineProps<{ findings: Finding[]; residualOnly?: boolean; actionable?: boolean }>()
const emit = defineEmits<{ select: [finding: Finding] }>()
const items = computed(() => summarizeFindings(props.findings, props.residualOnly))

const ICON: Record<FindingSeverity, IconName> = { error: 'alert', warning: 'warning', info: 'info' }

function sourceLabel(finding: Finding, text: string) {
  const location = finding.path ? `${finding.path}${finding.line ? `:${finding.line}` : ''}` : ''
  return [
    location && !text.includes(location) ? location : '',
    finding.module && !text.includes(finding.module) ? `import ${finding.module}` : '',
  ].filter(Boolean).join(' · ')
}
</script>

<template>
  <ul class="findings">
    <li v-for="f in items" :key="f.id" class="finding" :class="`finding--${f.severity}`">
      <BaseIcon :name="ICON[f.severity]" :size="18" class="finding__icon" />
      <div class="finding__body">
        <button v-if="actionable" type="button" class="finding__text finding__action" @click="emit('select', f.sources[0]!)">{{ f.text }}</button>
        <p v-else class="finding__text">{{ f.text }}</p>
        <div v-if="f.locations.length || f.modules.length" class="finding__details">
          <template v-for="source in f.sources" :key="source.id">
            <template v-if="sourceLabel(source, f.text)">
              <button v-if="actionable" type="button" class="finding__action" @click="emit('select', source)"><code>{{ sourceLabel(source, f.text) }}</code></button>
              <code v-else>{{ sourceLabel(source, f.text) }}</code>
            </template>
          </template>
        </div>
      </div>
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

.finding__body {
  min-width: 0;
}

.finding__action {
  text-align: left;
  text-decoration: underline;
  text-underline-offset: 3px;
}

.finding__action:focus-visible {
  outline: 2px solid currentColor;
  outline-offset: 4px;
}

.finding__details {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 12px;
  margin-top: 6px;
  color: var(--color-text-muted);
  font-size: 12px;
  overflow-wrap: anywhere;
}
</style>
