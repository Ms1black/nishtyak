<script setup lang="ts">
import { computed } from 'vue'
import { ProjectDropzone, useUploadProject } from '@/features/upload-project'
import { t } from '@/shared/i18n'
import { BaseButton } from '@/shared/ui'
import { useUploadWizardStore } from '../model/uploadWizardStore'
import SourceExampleCard from './SourceExampleCard.vue'

const wizard = useUploadWizardStore()
const { file, error, isUploading, canUpload, workspaceStatus, workspaceError, loadWorkspace, select, upload } = useUploadProject()

const hint = computed(() => {
  if (error.value) return t(error.value)
  return t('upload.hintUploading')
})

async function analyze() {
  const project = await upload()
  if (project) await wizard.openProject(project, { autoBuild: true })
}
</script>

<template>
  <div class="step-grid">
    <ProjectDropzone :file="file" :error="error && $t(error)" :disabled="isUploading" @select="select" />
    <SourceExampleCard />

    <div v-if="workspaceStatus !== 'ready'" class="upload-workspace" :role="workspaceError ? 'alert' : 'status'">
      <p>{{ $t(workspaceError ?? 'upload.workspace.loading') }}</p>
      <BaseButton v-if="workspaceError" variant="tonal" @click="loadWorkspace">{{ $t('upload.workspace.retry') }}</BaseButton>
    </div>
    <div class="step-actions">
      <p v-if="error || isUploading" class="step-actions__hint" :class="{ 'is-error': !!error }" aria-live="polite">{{ hint }}</p>
      <BaseButton :disabled="!canUpload" :loading="isUploading" @click="analyze">{{
        $t('upload.analyze')
      }}</BaseButton>
    </div>
  </div>
</template>

<style scoped src="./step.css"></style>
<style scoped>
.upload-workspace {
  grid-column: 1 / -1;
  color: var(--color-text-muted);
  font-size: 13px;
  line-height: 1.5;
}
.upload-workspace {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}
</style>
