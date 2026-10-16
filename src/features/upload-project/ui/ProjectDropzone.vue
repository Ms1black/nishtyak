<script setup lang="ts">
import { computed, ref } from 'vue'
import { formatBytes } from '@/shared/lib'
import { BaseIcon } from '@/shared/ui'
import { ACCEPTED_EXTENSIONS } from '../lib/validateProjectFile'

const props = defineProps<{
  file: File | null
  error?: string | null
  disabled?: boolean
}>()

const emit = defineEmits<{ select: [file: File | null] }>()

const input = ref<HTMLInputElement | null>(null)
const dragDepth = ref(0)
const isDragging = computed(() => dragDepth.value > 0)
const isZip = computed(() => props.file?.name.toLowerCase().endsWith('.zip') ?? false)

function openPicker() {
  if (!props.disabled) input.value?.click()
}

function onInputChange(event: Event) {
  const target = event.target as HTMLInputElement
  emit('select', target.files?.[0] ?? null)
  target.value = ''
}

function onDragEnter() {
  if (!props.disabled) dragDepth.value++
}

function onDragLeave() {
  dragDepth.value = Math.max(0, dragDepth.value - 1)
}

function onDrop(event: DragEvent) {
  dragDepth.value = 0
  if (props.disabled) return
  const dropped = event.dataTransfer?.files?.[0]
  if (dropped) emit('select', dropped)
}
</script>

<template>
  <div
    class="dropzone"
    :class="{ 'is-dragging': isDragging, 'has-file': !!file, 'has-error': !!error, 'is-disabled': disabled }"
    role="button"
    :tabindex="disabled ? -1 : 0"
    :aria-disabled="disabled"
    :aria-label="$t('upload.dropLabel')"
    @click="openPicker"
    @keydown.enter.prevent="openPicker"
    @keydown.space.prevent="openPicker"
    @dragenter.prevent="onDragEnter"
    @dragover.prevent
    @dragleave.prevent="onDragLeave"
    @drop.prevent="onDrop"
  >
    <input
      ref="input"
      type="file"
      class="visually-hidden"
      :accept="ACCEPTED_EXTENSIONS.join(',')"
      tabindex="-1"
      @change="onInputChange"
    />

    <span class="dropzone__icon">
      <svg v-if="!file" class="dropzone__clip" width="42" height="23" viewBox="0 0 42 23" fill="none" aria-hidden="true">
        <path
          d="M11.4583 22.9167C8.4194 22.9167 5.50492 21.7095 3.35607 19.5606C1.20721 17.4117 0 14.4973 0 11.4583C0 8.4194 1.20721 5.50492 3.35607 3.35607C5.50492 1.20721 8.4194 0 11.4583 0H33.3333C35.5435 0 37.6631 0.877973 39.2259 2.44078C40.7887 4.00358 41.6667 6.1232 41.6667 8.33333C41.6667 10.5435 40.7887 12.6631 39.2259 14.2259C37.6631 15.7887 35.5435 16.6667 33.3333 16.6667H15.625C14.2437 16.6667 12.9189 16.1179 11.9422 15.1412C10.9654 14.1644 10.4167 12.8397 10.4167 11.4583C10.4167 10.077 10.9654 8.75224 11.9422 7.77549C12.9189 6.79873 14.2437 6.25 15.625 6.25H31.25V9.375H15.625C15.0725 9.375 14.5426 9.59449 14.1519 9.9852C13.7612 10.3759 13.5417 10.9058 13.5417 11.4583C13.5417 12.0109 13.7612 12.5408 14.1519 12.9315C14.5426 13.3222 15.0725 13.5417 15.625 13.5417H33.3333C34.0173 13.5417 34.6946 13.407 35.3265 13.1452C35.9584 12.8835 36.5325 12.4998 37.0162 12.0162C37.4998 11.5325 37.8835 10.9584 38.1452 10.3265C38.407 9.69457 38.5417 9.0173 38.5417 8.33333C38.5417 7.64937 38.407 6.97209 38.1452 6.34019C37.8835 5.70829 37.4998 5.13412 37.0162 4.65049C36.5325 4.16685 35.9584 3.7832 35.3265 3.52146C34.6946 3.25972 34.0173 3.125 33.3333 3.125H11.4583C9.2482 3.125 7.12858 4.00297 5.56578 5.56578C4.00297 7.12858 3.125 9.2482 3.125 11.4583C3.125 13.6685 4.00297 15.7881 5.56578 17.3509C7.12858 18.9137 9.2482 19.7917 11.4583 19.7917H31.25V22.9167H11.4583Z"
          fill="currentColor"
        />
      </svg>
      <BaseIcon v-else :name="isZip ? 'archive' : 'file-code'" :size="34" />
    </span>

    <template v-if="file">
      <p class="dropzone__title">{{ file.name }}</p>
      <p class="dropzone__subtitle">{{ isZip ? $t('upload.zip') : $t('upload.pyFile') }} · {{ formatBytes(file.size) }}</p>
      <div class="dropzone__file-actions">
        <button type="button" class="dropzone__link" :disabled="disabled" @click.stop="openPicker">
          {{ $t('upload.chooseOther') }}
        </button>
        <span aria-hidden="true">·</span>
        <button type="button" class="dropzone__link" :disabled="disabled" @click.stop="emit('select', null)">
          {{ $t('upload.removeFile') }}
        </button>
      </div>
    </template>

    <template v-else>
      <p class="dropzone__title">{{ isDragging ? $t('upload.dropRelease') : $t('upload.dropTitle') }}</p>
      <p class="dropzone__subtitle">{{ $t('upload.dropHint') }}</p>
    </template>
  </div>
</template>

<style scoped>
.dropzone {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 352px;
  padding: 40px 24px;
  border: 1.5px dashed var(--color-border-strong);
  border-radius: var(--radius-xl);
  text-align: center;
  cursor: pointer;
  transition:
    border-color var(--transition),
    background-color var(--transition);
}

.dropzone:hover:not(.is-disabled),
.dropzone.is-dragging {
  border-color: var(--color-text-muted);
  background: rgb(255 255 255 / 0.02);
}

.dropzone.is-dragging {
  border-color: var(--color-text);
}

.dropzone.has-error {
  border-color: rgb(255 122 122 / 0.5);
}

.dropzone.is-disabled {
  cursor: progress;
  opacity: 0.7;
}

.dropzone__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 76px;
  height: 76px;
  border-radius: var(--radius-xl);
  background: var(--color-surface-raised);
}

.dropzone__clip {
  display: block;
  flex-shrink: 0;
}

.dropzone__title {
  max-width: 100%;
  margin-top: 24px;
  overflow: hidden;
  font-family: var(--font-display);
  font-size: 24px;
  font-weight: 500;
  line-height: 1.25;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dropzone__subtitle {
  margin-top: 10px;
  color: var(--color-text-secondary);
  font-size: 14px;
}

.dropzone__file-actions {
  display: flex;
  gap: 10px;
  margin-top: 16px;
  color: var(--color-text-subtle);
  font-size: 14px;
}

.dropzone__link {
  color: var(--color-text);
  text-decoration: underline;
  text-decoration-color: var(--color-text-subtle);
  text-underline-offset: 3px;
}

.dropzone__link:hover {
  text-decoration-color: currentColor;
}

@media (max-width: 640px) {
  .dropzone {
    min-height: 260px;
  }

  .dropzone__clip {
  display: block;
  flex-shrink: 0;
}

.dropzone__title {
    font-size: 20px;
  }
}
</style>
