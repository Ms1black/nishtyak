<script setup lang="ts">
import { computed, onScopeDispose, ref, watch } from 'vue'
import { getFileLanguage, getSecretLabel, isMarkdownFile, type ProjectFile } from '@/entities/project'
import { sessionGeneration } from '@/shared/api'
import { formatBytes } from '@/shared/lib'
import { BaseButton, BaseIcon, CodeBlock, MarkdownContent } from '@/shared/ui'
import { useFileContent } from '../model/useFileContent'

const props = defineProps<{
  projectId: string
  analysisId?: string
  file: ProjectFile | null
  excluded: boolean
  disabled?: boolean
}>()

const emit = defineEmits<{ toggle: [path: string] }>()

const selection = computed(() => ({ projectId: props.projectId, path: props.file?.path ?? null, analysisId: props.analysisId }))
const { content, isLoading, error, reload } = useFileContent(selection)
const mode = ref<'rendered' | 'source'>('rendered')
const copyStatus = ref<'copied' | 'error' | null>(null)
const markdown = computed(() => !!props.file && isMarkdownFile(props.file.path))
const language = computed(() => props.file ? getFileLanguage(props.file.path) : 'plaintext')
const isPlaceholder = computed(() => content.value?.kind === 'hidden' || content.value?.kind === 'binary')
let viewGeneration = 0
watch([selection, sessionGeneration], () => {
  viewGeneration += 1
  mode.value = 'rendered'
  copyStatus.value = null
}, { flush: 'sync' })
onScopeDispose(() => { viewGeneration += 1 })

async function copySource() {
  if (content.value?.kind !== 'text') return
  const view = viewGeneration
  const source = content.value.content
  copyStatus.value = null
  try {
    await navigator.clipboard.writeText(source)
    if (view === viewGeneration) copyStatus.value = 'copied'
  } catch {
    if (view === viewGeneration) copyStatus.value = 'error'
  }
}
</script>

<template>
  <section class="viewer" aria-live="polite">
    <header class="viewer__head">
      <h3 class="viewer__title">{{ $t('analysis.files.viewer') }}</h3>
      <template v-if="file">
        <span class="viewer__status" :class="{ 'is-excluded': excluded }">
          {{ excluded ? $t('analysis.files.excluded') : $t('analysis.files.inArchive') }}
        </span>
        <button type="button" class="viewer__toggle" :disabled="disabled" @click="emit('toggle', file.path)">
          {{ excluded ? $t('analysis.files.restore') : $t('analysis.files.exclude') }}
        </button>
      </template>
    </header>

    <p v-if="file" class="viewer__path">
      {{ file.path }} <span>· {{ formatBytes(file.size) }}</span>
    </p>

    <div v-if="markdown && content?.kind === 'text' && content.content" class="viewer__modes" role="group" :aria-label="$t('analysis.files.previewMode')">
      <BaseButton size="sm" variant="tonal" :aria-pressed="mode === 'rendered'" @click="mode = 'rendered'">{{ $t('analysis.files.rendered') }}</BaseButton>
      <BaseButton size="sm" variant="tonal" :aria-pressed="mode === 'source'" @click="mode = 'source'">{{ $t('analysis.files.source') }}</BaseButton>
      <BaseButton v-if="mode === 'rendered'" size="sm" variant="tonal" @click="copySource">{{ $t(copyStatus === 'copied' ? 'common.copied' : 'analysis.files.copySource') }}</BaseButton>
    </div>
    <p v-if="copyStatus === 'error'" class="viewer__note" role="alert">{{ $t('analysis.files.copyError') }}</p>

    <div class="viewer__body" :class="{ 'viewer__body--placeholder': isPlaceholder }">
      <p v-if="!file" class="viewer__empty">{{ $t('analysis.files.pickFile') }}</p>
      <p v-else-if="isLoading" class="viewer__empty">
        <BaseIcon name="refresh" :size="18" spin /> {{ $t('common.loading') }}
      </p>
      <div v-else-if="error" class="viewer__empty" role="alert">
        <p>{{ $t('analysis.files.openError') }}</p>
        <BaseButton size="sm" variant="tonal" @click="reload">{{ $t('common.retry') }}</BaseButton>
      </div>
      <div v-else-if="content?.kind === 'hidden'" class="viewer__unavailable">
        <BaseIcon name="lock" :size="28" />
        <p class="viewer__unavailable-title">{{ $t('analysis.files.unavailable') }}</p>
        <p class="viewer__unavailable-text">
          {{ $t('analysis.files.secretHidden', { secret: getSecretLabel(file.secret) }) }}
        </p>
      </div>
      <div v-else-if="content?.kind === 'binary'" class="viewer__unavailable">
        <BaseIcon name="file" :size="28" />
        <p class="viewer__unavailable-title">{{ $t('analysis.files.unavailable') }}</p>
        <p class="viewer__unavailable-text">{{ $t('analysis.files.binary') }}</p>
      </div>
      <p v-else-if="content?.kind === 'text' && !content.content" class="viewer__empty">
        {{ $t('analysis.files.emptyFile') }}
      </p>
      <template v-else-if="content?.kind === 'text'">
        <MarkdownContent v-if="markdown && mode === 'rendered'" class="viewer__markdown" :content="content.content" />
        <CodeBlock v-else :code="content.content" :language="language" :filename="file?.path" />
      </template>
      <p v-if="content?.kind === 'text' && content.truncated" class="viewer__note" role="note">
        {{ $t('workflow.truncatedFile') }}
      </p>
    </div>
  </section>
</template>

<style scoped>
.viewer {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.viewer__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  min-height: 32px;
}

.viewer__title {
  flex: 1;
  font-family: var(--font-display);
  font-size: 18px;
  font-weight: 500;
}

.viewer__status {
  display: inline-flex;
  align-items: center;
  height: 26px;
  padding: 0 12px;
  border-radius: var(--radius-pill);
  background: var(--color-surface-raised);
  color: var(--color-text-secondary);
  font-size: 12px;
}

.viewer__status.is-excluded {
  background: rgb(255 77 77 / 0.12);
  color: #ff5c5c;
}

.viewer__toggle {
  color: var(--color-text-secondary);
  font-size: 13px;
  text-decoration: underline;
  text-decoration-color: var(--color-text-subtle);
  text-underline-offset: 3px;
}

.viewer__toggle:hover:not(:disabled) {
  color: var(--color-text);
}

.viewer__path {
  margin-top: 6px;
  color: var(--color-text-secondary);
  font-family: var(--font-mono);
  font-size: 12px;
}

.viewer__path span {
  color: var(--color-text-subtle);
}

.viewer__body {
  flex: 1;
  margin-top: 16px;
  overflow: auto;
  max-height: 460px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg);
}

.viewer__empty {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 180px;
  padding: 24px;
  color: var(--color-text-muted);
  font-size: 14px;
  text-align: center;
}

.viewer__body--placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 320px;
  border-color: transparent;
  background: transparent;
}

.viewer__unavailable {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  max-width: 340px;
  padding: 24px;
  color: var(--color-text-subtle);
  text-align: center;
}

.viewer__unavailable-title {
  margin-top: 6px;
  color: var(--color-text-muted);
  font-size: 15px;
  font-weight: 500;
}

.viewer__unavailable-text {
  font-size: 13px;
  line-height: 1.45;
}

.viewer__modes {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.viewer__markdown {
  padding: 20px;
}

.viewer__note {
  padding: 12px 16px;
  color: var(--color-text-muted);
  font-size: 13px;
}
</style>
