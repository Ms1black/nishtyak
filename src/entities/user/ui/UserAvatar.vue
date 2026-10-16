<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { getInitials } from '../lib/initials'

const props = withDefaults(defineProps<{ name: string; src?: string | null; size?: number }>(), { size: 36, src: null })

const initials = computed(() => getInitials(props.name))
const failed = ref(false)
watch(
  () => props.src,
  () => (failed.value = false),
)
</script>

<template>
  <span
    class="avatar"
    :style="{ width: `${size}px`, height: `${size}px`, fontSize: `${Math.round(size * 0.36)}px` }"
    aria-hidden="true"
  >
    <img v-if="src && !failed" :src="src" alt="" class="avatar__img" @error="failed = true" />
    <template v-else>{{ initials }}</template>
  </span>
</template>

<style scoped>
.avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  overflow: hidden;
  border: 1px solid var(--color-border-strong);
  border-radius: 50%;
  background: var(--color-surface);
  font-weight: 600;
  letter-spacing: 0.02em;
}

.avatar__img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}
</style>
