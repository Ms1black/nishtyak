<script setup lang="ts">
import { computed } from 'vue'
import { ProjectDropzone, useUploadProject } from '@/features/upload-project'
import { t } from '@/shared/i18n'
import { BaseButton } from '@/shared/ui'
import { useUploadWizardStore } from '../model/uploadWizardStore'
import SourceExampleCard from './SourceExampleCard.vue'

const wizard = useUploadWizardStore()
const { file, error, isUploading, select, upload } = useUploadProject()

const hint = computed(() => {
  if (error.value) return t(error.value)
  if (isUploading.value) return t('upload.hintUploading')
  if (file.value) return t('upload.hintReady')
  return t('upload.hintSelect')
})

async function analyze() {
  const project = await upload()
  if (project) await wizard.openProject(project)
}
</script>

<template>
  <div class="step-grid">
    <ProjectDropzone :file="file" :error="error && $t(error)" :disabled="isUploading" @select="select" />
    <SourceExampleCard />

    <div class="step-actions">
      <p class="step-actions__hint" :class="{ 'is-error': !!error }" aria-live="polite">{{ hint }}</p>
      <BaseButton :disabled="!file" :loading="isUploading" @click="analyze">{{ $t('upload.analyze') }}</BaseButton>
    </div>
  </div>
</template>

<style scoped src="./step.css"></style>
