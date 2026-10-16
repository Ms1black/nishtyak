<script setup lang="ts">
import { computed, ref, watch } from 'vue'

const props = withDefaults(defineProps<{ name: string; src?: string | null; size?: number }>(), { size: 40, src: null })

const initial = computed(() => props.name.trim().charAt(0).toUpperCase() || '?')
const failed = ref(false)
watch(
  () => props.src,
  () => (failed.value = false),
)
</script>

<template>
  <span
    class="team-logo"
    :style="{ width: `${size}px`, height: `${size}px`, fontSize: `${Math.round(size * 0.4)}px` }"
    aria-hidden="true"
  >
    <img v-if="src && !failed" :src="src" alt="" class="team-logo__img" @error="failed = true" />
    <template v-else>{{ initial }}</template>
  </span>
</template>

<style scoped>
.team-logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  overflow: hidden;
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  font-weight: 600;
}

.team-logo__img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}
</style>
