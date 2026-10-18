<script setup lang="ts">
import { computed } from 'vue'
import { highlightCode } from '@/shared/lib/rendering/highlight'
import './code-theme.css'

const props = withDefaults(defineProps<{ code: string; language?: string }>(), {
  language: 'python',
})
const rendered = computed(() => highlightCode(props.code, props.language))
</script>

<template>
  <code class="inline-code syntax-content"><span class="hljs" v-html="rendered.html" /></code>
</template>

<style scoped>
.inline-code {
  display: inline;
  padding: 2px 5px;
  border: 1px solid #303744;
  border-radius: 4px;
  background: #1c222d;
  color: #dce2ec;
  font: 0.9em/1.6 var(--font-mono, monospace);
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
</style>
