<script setup lang="ts">
import { computed } from 'vue'
import { icons, type IconName } from './icons'

const props = withDefaults(
  defineProps<{
    name: IconName
    size?: number | string
    strokeWidth?: number
    spin?: boolean
  }>(),
  { size: 20, strokeWidth: 1.75, spin: false },
)

const markup = computed(() => icons[props.name])
</script>

<template>
  <svg
    class="icon"
    :class="{ spin }"
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    :stroke-width="strokeWidth"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    v-html="markup"
  />
</template>

<style scoped>
.icon {
  display: block;
  flex-shrink: 0;
}
</style>
