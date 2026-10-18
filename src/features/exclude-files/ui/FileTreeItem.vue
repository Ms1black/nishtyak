<script setup lang="ts">
import { getSecretLabel, type FileTreeNode } from '@/entities/project'
import { BaseIcon } from '@/shared/ui'

defineOptions({ name: 'FileTreeItem' })

const props = defineProps<{
  node: FileTreeNode
  depth: number
  selected: string | null
  excluded: Set<string>
  collapsed: Set<string>
}>()

const emit = defineEmits<{ select: [path: string]; toggleDir: [path: string] }>()

const isOpen = () => !props.collapsed.has(props.node.path)
</script>

<template>
  <li role="treeitem" :aria-expanded="node.type === 'dir' ? isOpen() : undefined" :aria-selected="node.path === selected">
    <button
      v-if="node.type === 'dir'"
      type="button"
      class="row row--dir"
      :style="{ paddingLeft: `${depth * 28}px` }"
      @click="emit('toggleDir', node.path)"
    >
      <BaseIcon name="chevron-down" :size="16" class="row__chevron" :class="{ 'is-closed': !isOpen() }" />
      <BaseIcon name="folder-tree" :size="20" />
      <span class="row__name">{{ node.name }}</span>
    </button>
    <button
      v-else
      type="button"
      class="row row--file"
      :class="{ 'is-selected': node.path === selected }"
      :style="{ paddingLeft: `${depth * 28 + 28}px` }"
      @click="emit('select', node.path)"
    >
      <BaseIcon name="file-code" :size="18" class="row__icon" />
      <span class="row__name row__name--mono">{{ node.name }}</span>
      <span v-if="node.file?.secret || excluded.has(node.path)" class="row__badges">
        <span v-if="excluded.has(node.path)" class="pill pill--danger">{{ $t('analysis.files.excluded') }}</span>
        <span v-if="node.file?.secret" class="pill" :class="{ 'pill--danger-strong': !excluded.has(node.path) }">
          {{ getSecretLabel(node.file.secret) }}
        </span>
      </span>
    </button>

    <ul v-if="node.type === 'dir' && isOpen()" role="group">
      <FileTreeItem
        v-for="child in node.children"
        :key="child.path"
        :node="child"
        :depth="depth + 1"
        :selected="selected"
        :excluded="excluded"
        :collapsed="collapsed"
        @select="emit('select', $event)"
        @toggle-dir="emit('toggleDir', $event)"
      />
    </ul>
  </li>
</template>

<style scoped>
.row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 40px;
  padding-top: 6px;
  padding-right: 10px;
  padding-bottom: 6px;
  border-radius: var(--radius-sm);
  text-align: left;
  transition: background-color var(--transition);
}

.row:hover {
  background: var(--color-surface-hover);
}

.row.is-selected {
  background: var(--color-surface-raised);
}

.row__chevron {
  color: var(--color-text-secondary);
  transition: transform var(--transition);
}

.row__chevron.is-closed {
  transform: rotate(-90deg);
}

.row__icon {
  color: var(--color-text-secondary);
}

.row.is-selected .row__icon {
  color: var(--color-text);
}

.row__name {
  font-size: 15px;
}

.row__name--mono {
  font-family: var(--font-mono);
  font-size: 14px;
}

.row.is-selected .row__name {
  font-weight: 600;
}

.row__badges {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 6px;
}

.pill {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 10px;
  border-radius: var(--radius-pill);
  background: var(--color-surface-raised);
  color: var(--color-text-muted);
  font-family: var(--font-sans);
  font-size: 11px;
  white-space: nowrap;
}

.pill--danger {
  background: rgb(255 77 77 / 0.12);
  color: #ff5c5c;
}

.pill--danger-strong {
  background: #ff5c5c;
  color: #000;
  font-weight: 500;
}
</style>
