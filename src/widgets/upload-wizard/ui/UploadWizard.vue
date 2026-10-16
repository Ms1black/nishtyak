<script setup lang="ts">
import { computed } from 'vue'
import { t } from '@/shared/i18n'
import { SegmentedControl, type SegmentedItem } from '@/shared/ui'
import { useUploadWizardStore, type WizardStep } from '../model/uploadWizardStore'
import StepAnalysis from './StepAnalysis.vue'
import StepReady from './StepReady.vue'
import StepUpload from './StepUpload.vue'

const wizard = useUploadWizardStore()

const steps = computed<SegmentedItem<WizardStep>[]>(() => [
  { value: 1, prefix: '01', label: t('wizard.steps.upload') },
  { value: 2, prefix: '02', label: t('wizard.steps.analysis'), disabled: !wizard.project },
  { value: 3, prefix: '03', label: t('wizard.steps.ready'), disabled: wizard.project?.archive.status !== 'ready' },
])

function goTo(step: WizardStep) {
  if (step === wizard.step) return
  if (step === 1) wizard.reset()
  else if (step === 2) wizard.backToAnalysis()
  else if (wizard.project?.archive.status === 'ready') wizard.step = 3
}
</script>

<template>
  <section class="wizard" :aria-label="$t('wizard.label')">
    <SegmentedControl
      :items="steps"
      :model-value="wizard.step"
      stretch
      :aria-label="$t('wizard.stepsLabel')"
      @update:model-value="goTo"
    />
    <div class="wizard__body">
      <Transition name="fade" mode="out-in">
        <StepUpload v-if="wizard.step === 1" key="upload" />
        <StepAnalysis v-else-if="wizard.step === 2" key="analysis" />
        <StepReady v-else key="ready" />
      </Transition>
    </div>
  </section>
</template>

<style scoped>
.wizard__body {
  margin-top: 44px;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 180ms ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

@media (max-width: 640px) {
  .wizard__body {
    margin-top: 24px;
  }

  .wizard :deep(.segmented__item:not(.is-active) .segmented__label) {
    display: none;
  }

  .wizard :deep(.segmented__item:not(.is-active)) {
    flex: 0 0 auto;
    padding: 0 16px;
  }
}
</style>
