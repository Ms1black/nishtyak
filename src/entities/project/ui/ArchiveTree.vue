<script setup lang="ts">
import { BaseIcon } from '@/shared/ui'

const props = defineProps<{
  rootName: string
  items: string[]
  selectable?: boolean
  selected?: string | null
  disabled?: boolean
}>()
const emit = defineEmits<{ select: [path: string] }>()

function select(path: string) {
  if (props.selectable && !props.disabled) emit('select', path)
}
</script>

<template>
  <div class="tree">
    <div class="tree__root">
      <BaseIcon name="archive" :size="24" />
      <span class="tree__root-name">{{ rootName }}</span>
    </div>
    <ul class="tree__list">
      <li v-for="item in items" :key="item" class="tree__item">
        <span class="tree__branch" aria-hidden="true">└</span>
        <BaseIcon name="file" :size="18" class="tree__file-icon" />
        <button
          v-if="selectable"
          type="button"
          class="tree__name tree__select"
          :class="{ 'is-selected': selected === item }"
          :aria-pressed="selected === item"
          :disabled="disabled"
          @click="select(item)"
        >{{ item }}</button>
        <span v-else class="tree__name">{{ item }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.tree {
  padding: 22px 24px;
  border-radius: var(--radius-lg);
  background: var(--color-bg);
}

.tree__root {
  display: flex;
  align-items: center;
  gap: 12px;
}

.tree__root-name {
  overflow: hidden;
  font-family: var(--font-mono);
  font-size: 15px;
  letter-spacing: 0.02em;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tree__list {
  display: flex;
  flex-direction: column;
  gap: 14px;
  margin-top: 16px;
}

.tree__item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-left: 4px;
  font-size: 15px;
}

.tree__branch {
  width: 12px;
  margin-top: -8px;
  color: var(--color-text-subtle);
  font-size: 13px;
}

.tree__file-icon {
  color: var(--color-text-secondary);
}

.tree__name {
  min-width: 0;
  overflow-wrap: anywhere;
  text-align: left;
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1.5;
}
.tree__select {
  border-radius: var(--radius-xs);
  padding: 4px 8px;
  margin: -4px -8px;
  color: var(--color-text-secondary);
  text-decoration: underline;
  text-decoration-color: var(--color-text-subtle);
  text-underline-offset: 3px;
}
.tree__select:hover:not(:disabled),
.tree__select.is-selected {
  color: var(--color-text);
  background: var(--color-surface-raised);
}
.tree__select:focus-visible {
  outline: 2px solid var(--color-text-secondary);
  outline-offset: 3px;
}
</style>
