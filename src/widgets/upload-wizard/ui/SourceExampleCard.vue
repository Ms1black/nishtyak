<script setup lang="ts">
import { ArchiveTree } from '@/entities/project'
import { BaseCard } from '@/shared/ui'
</script>

<template>
  <BaseCard tag="aside" class="example">
    <h2 class="example__title">{{ $t('upload.example.title') }}</h2>
    <ArchiveTree root-name="scientific-measurements.zip" :items="['experiment.py', 'calculations.py']" />
    <div class="example__downloads">
      <span>{{ $t('upload.example.download') }}</span>
      <a href="/examples/scientific-measurements.zip?raw=1" download>{{ $t('upload.example.scientific') }}</a>
      <a href="/examples/local-calibration.zip?raw=1" download>{{ $t('upload.example.local') }}</a>
    </div>
    <p class="example__note">{{ $t('upload.example.note') }}</p>
  </BaseCard>
</template>

<style scoped>
.example,
.example :deep(*) {
  font-family: var(--font-display);
}

.example :deep(.tree__root-name) {
  letter-spacing: normal;
}

.example {
  display: flex;
  flex-direction: column;
  gap: 28px;
}

.example__title {
  font-family: var(--font-display);
  font-size: 23px;
  font-weight: 500;
  line-height: 1.2;
}

.example__note {
  padding-top: 24px;
  border-top: 1px solid var(--color-border-strong);
  color: var(--color-text-muted);
  font-size: 13px;
  line-height: 1.4;
}

.example__downloads {
  display: flex;
  flex-direction: column;
  gap: 12px;
  font-size: 14px;
}

.example__downloads a {
  color: var(--color-text);
  text-decoration: underline;
  text-underline-offset: 4px;
}
</style>
