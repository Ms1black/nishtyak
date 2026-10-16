<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useUserStore } from '@/entities/user'
import BlankLayout from './layouts/BlankLayout.vue'
import MainLayout from './layouts/MainLayout.vue'

const route = useRoute()
const userStore = useUserStore()

const layout = computed(() =>
  route.meta.layout === 'blank' || (route.meta.public && !userStore.isAuthenticated) ? BlankLayout : MainLayout,
)
</script>

<template>
  <component :is="layout">
    <RouterView />
  </component>
</template>
