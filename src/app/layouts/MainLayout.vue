<script setup lang="ts">
import { onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useTeamStore } from '@/entities/team'
import { AppFooter } from '@/widgets/app-footer'
import { AppHeader } from '@/widgets/app-header'
import { AppNav } from '@/widgets/app-nav'

const route = useRoute()
const teamStore = useTeamStore()

onMounted(() => teamStore.init())
</script>

<template>
  <div class="layout">
    <AppHeader />
    <AppNav />
    <main class="layout__main">
      <slot />
    </main>
    <div class="container">
      <AppFooter :note="route.meta.footerNote" />
    </div>
  </div>
</template>

<style scoped>
.layout {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}

.layout__main {
  flex: 1;
  padding-block: 80px 120px;
}

@media (max-width: 640px) {
  .layout__main {
    padding-block: 32px;
  }
}
</style>
