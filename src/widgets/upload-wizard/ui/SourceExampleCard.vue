<script setup lang="ts">
import { ArchiveTree } from '@/entities/project'
import { BaseCard } from '@/shared/ui'
</script>

<template>
  <BaseCard tag="aside" class="example">
    <h2 class="example__title">{{ $t('upload.example.title') }}</h2>
    <ArchiveTree root-name="scientific-measurements.zip" :items="['experiment.py', 'calculations.py']" />
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

</style>
