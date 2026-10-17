<script setup lang="ts">
import { computed } from 'vue'
import { renderMarkdown } from '@/shared/lib/rendering/markdown'
import { t } from '@/shared/i18n'
import './code-theme.css'

const props = defineProps<{ content: string }>()
const rendered = computed(() => renderMarkdown(props.content))
</script>

<template>
  <div class="markdown-content syntax-content">
    <div class="markdown-content__body" v-html="rendered.html" />
    <p v-if="rendered.truncated" class="markdown-content__notice">
      {{ t('rendering.documentTruncated') }}
    </p>
  </div>
</template>

<style scoped>
.markdown-content {
  min-width: 0;
  color: var(--color-text-secondary, #c2c2c2);
  font-size: 14px;
  line-height: 1.75;
  overflow-wrap: anywhere;
}
.markdown-content__body :deep(> :first-child) {
  margin-top: 0;
}
.markdown-content__body :deep(> :last-child) {
  margin-bottom: 0;
}
.markdown-content__body :deep(p),
.markdown-content__body :deep(ul),
.markdown-content__body :deep(ol),
.markdown-content__body :deep(blockquote),
.markdown-content__body :deep(table),
.markdown-content__body :deep(pre) {
  margin: 14px 0;
}
.markdown-content__body :deep(h1),
.markdown-content__body :deep(h2),
.markdown-content__body :deep(h3),
.markdown-content__body :deep(h4),
.markdown-content__body :deep(h5),
.markdown-content__body :deep(h6) {
  margin: 24px 0 12px;
  color: var(--color-text, #fff);
  font-weight: 600;
  line-height: 1.35;
}
.markdown-content__body :deep(h1) {
  padding-bottom: 10px;
  border-bottom: 1px solid var(--color-border, #2b2b2b);
  font-size: 26px;
}
.markdown-content__body :deep(h2) {
  font-size: 21px;
}
.markdown-content__body :deep(h3) {
  font-size: 17px;
}
.markdown-content__body :deep(h4),
.markdown-content__body :deep(h5),
.markdown-content__body :deep(h6) {
  font-size: 15px;
}
.markdown-content__body :deep(ul),
.markdown-content__body :deep(ol) {
  padding-left: 24px;
}
.markdown-content__body :deep(ul) {
  list-style: disc;
}
.markdown-content__body :deep(ol) {
  list-style: decimal;
}
.markdown-content__body :deep(li) {
  margin: 5px 0;
}
.markdown-content__body :deep(li > p) {
  margin: 5px 0;
}
.markdown-content__body :deep(li > ul),
.markdown-content__body :deep(li > ol) {
  margin: 5px 0;
}
.markdown-content__body :deep(a) {
  color: #8ab4ff;
  text-decoration: underline;
  text-underline-offset: 3px;
}
.markdown-content__body :deep(a:hover) {
  color: #bfd4ff;
}
.markdown-content__body :deep(blockquote) {
  padding: 4px 16px;
  border-left: 3px solid #617ca6;
  color: #aab8cc;
  background: #131923;
}
.markdown-content__body :deep(:not(pre) > code) {
  padding: 2px 5px;
  border: 1px solid #303744;
  border-radius: 4px;
  background: #1c222d;
  color: #e5c6f5;
  font: 0.9em var(--font-mono, monospace);
}
.markdown-content__body :deep(pre) {
  max-height: 520px;
  overflow: auto;
  padding: 14px 16px;
  border: 1px solid #2a303b;
  border-radius: 8px;
  background: #101218;
  font: 12px/1.75 var(--font-mono, monospace);
  white-space: pre;
  tab-size: 4;
}
.markdown-content__body :deep(pre code) {
  font: inherit;
}
.markdown-content__body :deep(table) {
  display: block;
  width: max-content;
  max-width: 100%;
  overflow: auto;
  border-collapse: collapse;
  font-size: 13px;
}
.markdown-content__body :deep(th),
.markdown-content__body :deep(td) {
  padding: 9px 12px;
  border: 1px solid var(--color-border-strong, #3d3d3d);
  text-align: left;
}
.markdown-content__body :deep(th) {
  background: #1b2029;
  color: #e6edf7;
  font-weight: 600;
}
.markdown-content__body :deep(hr) {
  margin: 24px 0;
  border: 0;
  border-top: 1px solid var(--color-border, #2b2b2b);
}
.markdown-content__body :deep(.markdown-image) {
  color: var(--color-text-muted, #8f8f8f);
  font-style: italic;
}
.markdown-content__notice {
  margin-top: 14px;
  color: var(--color-text-muted, #8f8f8f);
  font-size: 12px;
}
</style>
