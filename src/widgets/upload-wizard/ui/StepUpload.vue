<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { projectApi } from '@/entities/project'
import { ProjectDropzone, useUploadProject } from '@/features/upload-project'
import { t } from '@/shared/i18n'
import { BaseButton } from '@/shared/ui'
import { useUploadWizardStore } from '../model/uploadWizardStore'
import SourceExampleCard from './SourceExampleCard.vue'

const wizard = useUploadWizardStore()
const { file, error, isUploading, select, upload } = useUploadProject()
const automaticReviewEnabled = ref<boolean | null>(null)

onMounted(async () => {
  const capabilities = await projectApi.capabilities().catch(() => null)
  automaticReviewEnabled.value = capabilities?.automaticReview.enabled ?? null
})

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

    <p v-if="automaticReviewEnabled !== false" class="upload-disclosure">
      {{
        $t(automaticReviewEnabled ? 'upload.automaticReviewDisclosure' : 'upload.automaticReviewConditional')
      }}
    </p>
    <div class="step-actions">
      <p class="step-actions__hint" :class="{ 'is-error': !!error }" aria-live="polite">{{ hint }}</p>
      <BaseButton :disabled="!file" :loading="isUploading" @click="analyze">{{
        $t('upload.analyze')
      }}</BaseButton>
    </div>
  </div>
</template>

<style scoped src="./step.css"></style>
<style scoped>
.upload-disclosure {
  grid-column: 1 / -1;
  color: var(--color-text-muted);
  font-size: 13px;
  line-height: 1.5;
}
</style>
