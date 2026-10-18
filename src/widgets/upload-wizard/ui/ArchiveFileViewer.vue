<script setup lang="ts">
import { computed, onScopeDispose, ref, watch } from 'vue'
import { getFileLanguage, isMarkdownFile } from '@/entities/project'
import { BaseButton, BaseDialog, BaseIcon, CodeBlock, MarkdownContent } from '@/shared/ui'
import { useArchiveFilePreview } from '../model/archiveFilePreview'

const props = defineProps<{ projectId: string; archiveTag?: string; path: string | null }>()
const open = defineModel<boolean>('open', { required: true })
const emit = defineEmits<{ refresh: [] }>()
const selection = computed(() => ({ projectId: props.projectId, archiveTag: props.archiveTag, path: props.path }))
const preview = useArchiveFilePreview(selection, open)
const state = preview.state
const rendered = ref(true)
const copyStatus = ref<'copied' | 'error' | null>(null)
let copyRequest = 0
const markdown = computed(() => isMarkdownFile(props.path ?? ''))
const language = computed(() => getFileLanguage(props.path ?? ''))

watch([open, selection], () => {
  rendered.value = true
  copyStatus.value = null
  copyRequest += 1
}, { flush: 'sync' })
onScopeDispose(() => { copyRequest += 1 })

async function copySource() {
  if (state.content?.kind !== 'text') return
  const currentRequest = ++copyRequest
  try {
    await navigator.clipboard.writeText(state.content.content)
    if (currentRequest === copyRequest) copyStatus.value = 'copied'
  } catch {
    if (currentRequest === copyRequest) copyStatus.value = 'error'
  }
}
</script>

<template>
  <BaseDialog v-model:open="open" size="wide" :title="$t('ready.files.title')">
    <div class="archive-file">
      <p class="archive-file__path">{{ path }}</p>
      <p v-if="state.isLoading" class="archive-file__state" role="status">
        <BaseIcon name="refresh" :size="20" spin /> {{ $t('common.loading') }}
      </p>
      <div v-else-if="state.error" class="archive-file__state" role="alert">
        <p>{{ $t(state.error) }}</p>
        <BaseButton v-if="state.needsRefresh" variant="tonal" @click="emit('refresh')">{{ $t('ready.files.refresh') }}</BaseButton>
        <BaseButton v-else variant="tonal" icon="refresh" @click="preview.retry()">{{ $t('common.retry') }}</BaseButton>
      </div>
      <p v-else-if="state.content?.kind === 'hidden'" class="archive-file__state">{{ $t('ready.files.hidden') }}</p>
      <p v-else-if="state.content?.kind === 'binary'" class="archive-file__state">{{ $t('analysis.files.binary') }}</p>
      <template v-else-if="state.content?.kind === 'text'">
        <div v-if="markdown" class="archive-file__modes" role="group" :aria-label="$t('analysis.files.previewMode')">
          <BaseButton size="sm" variant="tonal" :aria-pressed="rendered" @click="rendered = true">{{ $t('analysis.files.rendered') }}</BaseButton>
          <BaseButton size="sm" variant="tonal" :aria-pressed="!rendered" @click="rendered = false">{{ $t('analysis.files.source') }}</BaseButton>
          <BaseButton v-if="rendered" size="sm" variant="tonal" @click="copySource">{{ $t('analysis.files.copySource') }}</BaseButton>
        </div>
        <p v-if="copyStatus" class="archive-file__hint" role="status">{{ $t(copyStatus === 'copied' ? 'common.copied' : 'analysis.files.copyError') }}</p>
        <p v-if="state.content.truncated" class="archive-file__hint" role="note">{{ $t('workflow.truncatedFile') }}</p>
        <p v-if="!state.content.content" class="archive-file__state">{{ $t('analysis.files.emptyFile') }}</p>
        <MarkdownContent v-else-if="markdown && rendered" :content="state.content.content" />
        <CodeBlock v-else :code="state.content.content" :language="language" :filename="path ?? undefined" />
      </template>
    </div>
    <template #actions>
      <BaseButton variant="tonal" @click="open = false">{{ $t('common.close') }}</BaseButton>
    </template>
  </BaseDialog>
</template>

<style scoped>
.archive-file {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}
.archive-file__path {
  font-family: var(--font-mono);
  font-size: 14px;
  overflow-wrap: anywhere;
}
.archive-file__hint { color: var(--color-text-muted); font-size: 13px; }
.archive-file__state {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 12px;
  padding: 24px 0;
}
.archive-file__modes { display: flex; flex-wrap: wrap; gap: 10px; }
</style>
