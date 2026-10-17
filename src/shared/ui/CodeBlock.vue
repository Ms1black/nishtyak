<script setup lang="ts">
import { computed, onScopeDispose, ref } from 'vue'
import { highlightCode } from '@/shared/lib/rendering/highlight'
import { t } from '@/shared/i18n'
import BaseIcon from './BaseIcon.vue'
import './code-theme.css'

const props = withDefaults(
  defineProps<{
    code: string
    language?: string
    filename?: string
    lineNumbers?: boolean
  }>(),
  { lineNumbers: true },
)

const rendered = computed(() => highlightCode(props.code, props.language, props.filename))
const wrap = ref(false)
const copyState = ref<'idle' | 'copied' | 'failed'>('idle')
let timer: ReturnType<typeof setTimeout> | undefined
onScopeDispose(() => clearTimeout(timer))

async function copy() {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(props.code)
    } else {
      const field = document.createElement('textarea')
      field.value = props.code
      field.style.position = 'fixed'
      field.style.opacity = '0'
      document.body.appendChild(field)
      try {
        field.select()
        if (!document.execCommand('copy')) throw new Error('clipboard_unavailable')
      } finally {
        field.remove()
      }
    }
    copyState.value = 'copied'
  } catch {
    copyState.value = 'failed'
  }
  clearTimeout(timer)
  timer = setTimeout(() => {
    copyState.value = 'idle'
  }, 2000)
}
</script>

<template>
  <div class="code-block syntax-content" :class="{ 'code-block--wrap': wrap }">
    <div class="code-block__toolbar">
      <span v-if="filename" class="code-block__filename" :title="filename">{{ filename }}</span>
      <span class="code-block__language">{{ rendered.language }}</span>
      <div class="code-block__actions">
        <button
          type="button"
          class="code-block__button"
          :aria-pressed="wrap"
          :title="t('rendering.wrapLines')"
          :aria-label="t('rendering.wrapLines')"
          @click="wrap = !wrap"
        >
          ↵
        </button>
        <button
          type="button"
          class="code-block__button"
          :disabled="!code"
          :title="t('rendering.copyCode')"
          :aria-label="t('rendering.copyCode')"
          @click="copy"
        >
          <BaseIcon :name="copyState === 'copied' ? 'check' : 'copy'" :size="14" />
        </button>
      </div>
      <span class="visually-hidden" role="status">{{
        copyState === 'copied'
          ? t('rendering.copied')
          : copyState === 'failed'
            ? t('rendering.copyFailed')
            : ''
      }}</span>
    </div>
    <div
      class="code-block__viewport"
      tabindex="0"
      role="region"
      :aria-label="filename || t('rendering.code')"
    >
      <div v-if="lineNumbers" class="code-block__numbers" aria-hidden="true">
        <span v-for="line in rendered.lineCount" :key="line">{{ line }}</span>
      </div>
      <pre
        class="code-block__source"
      ><code class="hljs" :class="`language-${rendered.language}`" v-html="rendered.html" /></pre>
    </div>
    <p v-if="rendered.truncated" class="code-block__notice">{{ t('rendering.codeTruncated') }}</p>
  </div>
</template>

<style scoped>
.code-block {
  min-width: 0;
  overflow: hidden;
  border: 1px solid var(--color-border, #2b2b2b);
  border-radius: var(--radius-sm, 8px);
  background: #101218;
  font-family: var(--font-mono, monospace);
}
.code-block__toolbar {
  min-height: 38px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 5px 10px 5px 14px;
  border-bottom: 1px solid #252b35;
  background: #171b23;
}
.code-block__filename {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #dce2ec;
  font-size: 12px;
}
.code-block__language {
  flex-shrink: 0;
  color: #8d98aa;
  font-size: 11px;
  text-transform: lowercase;
}
.code-block__actions {
  display: flex;
  flex-shrink: 0;
  gap: 4px;
  margin-left: auto;
}
.code-block__button {
  display: grid;
  width: 27px;
  height: 27px;
  place-items: center;
  border-radius: 5px;
  color: #aeb8c9;
  font: 16px var(--font-mono, monospace);
}
.code-block__button:hover,
.code-block__button[aria-pressed='true'] {
  color: #f2f5fa;
  background: #2a3140;
}
.code-block__button:disabled {
  opacity: 0.35;
}
.code-block__viewport {
  display: flex;
  max-height: 560px;
  overflow: auto;
  font-size: 12px;
  line-height: 1.75;
  tab-size: 4;
}
.code-block__numbers {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  min-width: 46px;
  padding: 14px 10px 14px 12px;
  border-right: 1px solid #202631;
  color: #647084;
  text-align: right;
  user-select: none;
}
.code-block__numbers span {
  display: block;
}
.code-block__source {
  flex: 1;
  min-width: max-content;
  margin: 0;
  padding: 14px 16px;
  color: #dce2ec;
  font: inherit;
  white-space: pre;
}
.code-block__source code {
  font: inherit;
}
.code-block--wrap .code-block__source {
  min-width: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.code-block--wrap .code-block__numbers {
  display: none;
}
.code-block__notice {
  margin: 0;
  padding: 9px 14px;
  border-top: 1px solid #252b35;
  color: #a5afbe;
  font: 11px/1.5 var(--font-sans, sans-serif);
}
</style>
