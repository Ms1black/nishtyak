<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { buildFileTree, type ProjectFile } from '@/entities/project'
import { t } from '@/shared/i18n'
import { BaseIcon } from '@/shared/ui'
import FileTreeItem from './FileTreeItem.vue'
import FileViewer from './FileViewer.vue'

const props = defineProps<{
  projectId: string
  analysisId?: string
  projectName: string
  files: ProjectFile[]
  excludedFiles: string[]
  disabled?: boolean
}>()

const emit = defineEmits<{ toggle: [path: string] }>()

const isCollapsed = ref(false)
const collapsedDirs = reactive(new Set<string>())
const selectedPath = ref<string | null>(null)

const tree = computed(() => buildFileTree(props.files))
const excluded = computed(() => new Set(props.excludedFiles))
const selectedFile = computed(() => props.files.find((f) => f.path === selectedPath.value) ?? null)

const summary = computed(() => {
  const total = props.files.length
  const off = props.excludedFiles.length
  const files = t('units.files', total)
  return off ? `${files} · ${t('analysis.files.excludedCount', off)}` : files
})

watch(
  () => props.files,
  (files) => {
    if (!selectedPath.value || !files.some((f) => f.path === selectedPath.value)) {
      selectedPath.value = (files.find((f) => f.secret) ?? files.find((f) => f.path.endsWith('.py')) ?? files[0])?.path ?? null
    }
  },
  { immediate: true },
)

function toggleDir(path: string) {
  if (collapsedDirs.has(path)) collapsedDirs.delete(path)
  else collapsedDirs.add(path)
}

function focusFile(path: string) {
  isCollapsed.value = false
  selectedPath.value = path
}

defineExpose({ focusFile })
</script>

<template>
  <div class="explorer" :class="{ 'is-collapsed': isCollapsed }">
    <div v-if="isCollapsed" class="explorer__collapsed">
      <h3 class="explorer__title">{{ $t('analysis.files.title') }}</h3>
      <span class="explorer__summary">{{ summary }}</span>
    </div>

    <div v-else class="explorer__grid">
      <div class="explorer__tree">
        <h3 class="explorer__title">{{ $t('analysis.files.title') }}</h3>
        <ul role="tree" class="tree" :aria-label="$t('analysis.files.treeLabel', { name: projectName })">
          <li role="treeitem" aria-expanded="true">
            <div class="tree__root">
              <BaseIcon name="chevron-down" :size="16" class="tree__root-chevron" />
              <BaseIcon name="folder-tree" :size="20" />
              <span>{{ projectName }}</span>
            </div>
            <ul role="group">
              <FileTreeItem
                v-for="node in tree"
                :key="node.path"
                :node="node"
                :depth="1"
                :selected="selectedPath"
                :excluded="excluded"
                :collapsed="collapsedDirs"
                @select="selectedPath = $event"
                @toggle-dir="toggleDir"
              />
            </ul>
          </li>
        </ul>
      </div>

      <FileViewer
        :project-id="projectId"
        :analysis-id="analysisId"
        :file="selectedFile"
        :excluded="!!selectedFile && excluded.has(selectedFile.path)"
        :disabled="disabled"
        @toggle="emit('toggle', $event)"
      />
    </div>

    <button type="button" class="explorer__collapse" :aria-expanded="!isCollapsed" @click="isCollapsed = !isCollapsed">
      {{ isCollapsed ? $t('analysis.files.expand') : $t('analysis.files.collapse') }}
    </button>
  </div>
</template>

<style scoped>
.explorer__grid {
  display: grid;
  grid-template-columns: minmax(280px, 0.4fr) minmax(0, 0.6fr);
  gap: 32px 48px;
}

.explorer__title {
  font-family: var(--font-display);
  font-size: 18px;
  font-weight: 500;
}

.tree {
  margin-top: 12px;
}

.tree__root {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 40px;
  font-size: 15px;
}

.tree__root-chevron {
  color: var(--color-text-secondary);
}

.explorer__collapsed {
  display: flex;
  align-items: baseline;
  gap: 16px;
}

.explorer__summary {
  color: var(--color-text-muted);
  font-size: 14px;
}

.explorer__collapse {
  display: block;
  width: calc(100% - 96px);
  height: 30px;
  margin: 28px auto 0;
  border-radius: var(--radius-pill);
  background: var(--color-surface-raised);
  color: var(--color-text-muted);
  font-size: 13px;
  transition: color var(--transition);
}

.explorer__collapse:hover {
  color: var(--color-text);
}

.is-collapsed .explorer__collapse {
  margin-top: 20px;
}

@media (max-width: 900px) {
  .explorer__grid {
    grid-template-columns: 1fr;
  }

  .explorer__collapse {
    width: 100%;
  }
}
</style>
