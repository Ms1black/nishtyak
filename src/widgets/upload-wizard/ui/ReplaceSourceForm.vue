<script setup lang="ts">
import { ref } from 'vue'
import { validateProjectFile } from '@/features/upload-project'
import { BaseButton } from '@/shared/ui'

defineProps<{ disabled?: boolean }>()
const emit = defineEmits<{ replace: [file: File] }>()
const file = ref<File | null>(null)
const error = ref<string | null>(null)

function select(event: Event) {
  const input = event.target as HTMLInputElement
  const files = input.files
  file.value = null
  error.value = null
  if (!files?.length) return
  if (files.length !== 1) error.value = 'upload.errors.multiple'
  else {
    const selected = files.item(0)
    if (selected) {
      error.value = validateProjectFile(selected)
      if (!error.value) file.value = selected
    }
  }
  input.value = ''
}

function submit() {
  if (file.value && !error.value) emit('replace', file.value)
}
</script>

<template>
  <form class="replace-source" @submit.prevent="submit">
    <h3>{{ $t('upload.replace.title') }}</h3>
    <p>{{ $t('upload.replace.hint') }}</p>
    <label>
      <span>{{ $t('upload.replace.choose') }}</span>
      <input type="file" accept=".zip,application/zip" :disabled="disabled" @change="select">
    </label>
    <p v-if="file" class="replace-source__name">{{ file.name }}</p>
    <p v-if="error" role="alert">{{ $t(error) }}</p>
    <BaseButton variant="tonal" type="submit" :disabled="disabled || !file">{{ $t('upload.replace.submit') }}</BaseButton>
  </form>
</template>

<style scoped>
.replace-source,
.replace-source label {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.replace-source {
  margin-top: 20px;
  padding-top: 20px;
  border-top: 1px solid var(--color-border);
  font-size: 14px;
}
.replace-source p {
  color: var(--color-text-muted);
  font-size: 13px;
  line-height: 1.5;
}
.replace-source .button { align-self: flex-start; }
.replace-source__name { overflow-wrap: anywhere; }
</style>
