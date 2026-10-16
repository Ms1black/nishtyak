<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { getSecretLabel, projectApi, type FileContent, type ProjectFile } from '@/entities/project'
import { formatBytes } from '@/shared/lib'
import { BaseIcon } from '@/shared/ui'

const props = defineProps<{
  projectId: string
  file: ProjectFile | null
  excluded: boolean
  disabled?: boolean
}>()

const emit = defineEmits<{ toggle: [path: string] }>()

const content = ref<FileContent | null>(null)
const isLoading = ref(false)
const error = ref(false)

const lines = computed(() => (content.value?.kind === 'text' ? content.value.content.split('\n') : []))

const isPlaceholder = computed(() => content.value?.kind === 'hidden' || content.value?.kind === 'binary')

watch(
  () => props.file?.path,
  async (path) => {
    content.value = null
    error.value = false
    if (!path) return
    isLoading.value = true
    try {
      const result = await projectApi.getFileContent(props.projectId, path)
      if (props.file?.path === path) content.value = result
    } catch {
      error.value = true
    } finally {
      isLoading.value = false
    }
  },
  { immediate: true },
)
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

    <div class="viewer__body" :class="{ 'viewer__body--placeholder': isPlaceholder }">
      <p v-if="!file" class="viewer__empty">{{ $t('analysis.files.pickFile') }}</p>
      <p v-else-if="isLoading" class="viewer__empty"><BaseIcon name="refresh" :size="18" spin /> {{ $t('common.loading') }}</p>
      <p v-else-if="error" class="viewer__empty">{{ $t('analysis.files.openError') }}</p>
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
      <p v-else-if="content?.kind === 'text' && !content.content" class="viewer__empty">{{ $t('analysis.files.emptyFile') }}</p>
      <ol v-else-if="content?.kind === 'text'" class="code">
        <li v-for="(line, index) in lines" :key="index" class="code__line">
          <span class="code__number">{{ index + 1 }}</span>
          <span class="code__text">{{ line || ' ' }}</span>
        </li>
      </ol>
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

.code {
  margin: 0;
  padding: 14px 0;
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 28px;
  counter-reset: none;
}

.code__line {
  display: flex;
  gap: 24px;
  padding: 0 20px 0 14px;
  white-space: pre;
}

.code__number {
  width: 24px;
  flex-shrink: 0;
  color: var(--color-text-subtle);
  font-size: 11px;
  text-align: right;
  user-select: none;
}
</style>
