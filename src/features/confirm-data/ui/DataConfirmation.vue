<script setup lang="ts">
import { computed, ref } from 'vue'
import { describeFinding, type Finding } from '@/entities/project'
import { BaseButton, BaseTextarea, InlineCode } from '@/shared/ui'

const props = withDefaults(defineProps<{
  dataFiles: string[]
  inputFindings?: Finding[]
  canReplace?: boolean
  disabled?: boolean
}>(), { inputFindings: () => [] })

const emit = defineEmits<{ replaceSource: [] }>()
const inputDescription = defineModel<string>('inputDescription', { required: true })
const expectedOutput = defineModel<string>('expectedOutput', { required: true })
const description = defineModel<string>('description', { required: true })
const additionsOpen = ref(false)
const hasAdditions = computed(() => [inputDescription.value, expectedOutput.value, description.value].some((value) => value.trim()))
const inputNotes = computed(() => [...new Map(props.inputFindings.map((finding) => [finding.message, finding])).values()])

function toggleAdditions(event: Event) {
  if (event.target === event.currentTarget) additionsOpen.value = (event.target as HTMLDetailsElement).open
}
function openAdditions() { additionsOpen.value = true }
defineExpose({ openAdditions })
</script>

<template>
  <div class="data">
    <div class="data__facts">
      <p>{{ $t(dataFiles.length ? 'analysis.data.found' : 'analysis.data.notFound') }}</p>
      <ul v-if="dataFiles.length" class="data__files">
        <li v-for="file in dataFiles" :key="file"><InlineCode :code="file" language="plaintext" /></li>
      </ul>
      <p class="data__note">{{ $t('analysis.data.automaticHint') }}</p>
    </div>
    <div v-if="inputNotes.length" class="data__missing" role="note">
      <h3>{{ $t('analysis.data.missingTitle') }}</h3>
      <ul><li v-for="finding in inputNotes" :key="finding.id">{{ describeFinding(finding) }}</li></ul>
      <BaseButton v-if="canReplace" variant="tonal" :disabled="disabled" @click="emit('replaceSource')">
        {{ $t('analysis.data.replaceZip') }}
      </BaseButton>
    </div>
    <details class="data__additions" :open="additionsOpen" @toggle="toggleAdditions">
      <summary>{{ $t('analysis.data.additionsTitle') }}<span v-if="hasAdditions"> · {{ $t('analysis.data.savedAdditions') }}</span></summary>
      <div v-if="additionsOpen" class="data__fields">
        <p class="data__note">{{ $t('analysis.data.additionsHint') }}</p>
        <div class="data__field">
          <label for="data-input">{{ $t('analysis.data.input') }}</label>
          <BaseTextarea id="data-input" v-model="inputDescription" :rows="2" :maxlength="8192" :placeholder="$t('analysis.data.inputPlaceholder')" :disabled="disabled" />
        </div>
        <div class="data__field">
          <label for="data-output">{{ $t('analysis.data.output') }}</label>
          <BaseTextarea id="data-output" v-model="expectedOutput" :rows="2" :maxlength="8192" :placeholder="$t('analysis.data.outputPlaceholder')" :disabled="disabled" />
        </div>
        <div class="data__field">
          <label for="data-description">{{ $t('analysis.data.description') }}</label>
          <BaseTextarea id="data-description" v-model="description" :rows="2" :maxlength="8192" :placeholder="$t('analysis.data.descriptionPlaceholder')" :disabled="disabled" />
        </div>
        <p class="data__note">{{ $t('analysis.data.note') }}</p>
      </div>
    </details>
  </div>
</template>

<style scoped>
.data, .data__fields, .data__field, .data__facts, .data__missing { display: flex; flex-direction: column; gap: 12px; }
.data { gap: 20px; }
.data__files { display: flex; flex-wrap: wrap; gap: 8px; list-style: none; padding: 0; }
.data__missing { padding: 16px; border: 1px solid var(--color-border); border-radius: var(--radius-md); }
.data__missing h3 { font-size: 14px; font-weight: 500; }
.data__missing ul { padding-left: 20px; line-height: 1.6; }
.data__missing .button { align-self: flex-start; }
.data__additions > summary { cursor: pointer; font-size: 14px; }
.data__additions > summary span, .data__note { color: var(--color-text-muted); font-size: 13px; line-height: 1.5; }
.data__fields { margin-top: 16px; gap: 20px; }
.data__field label { font-size: 14px; font-weight: 500; }
</style>
