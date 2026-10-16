<script setup lang="ts">
import { UploadWizard } from '@/widgets/upload-wizard'
import { useProjectRoute } from '../model/useProjectRoute'

useProjectRoute()
</script>

<template>
  <div class="container upload-page">
    <h1 class="visually-hidden">{{ $t('routes.upload') }}</h1>
    <UploadWizard />
  </div>
</template>
