<script setup lang="ts">
import { BaseCheckbox, BaseTextarea } from '@/shared/ui'

defineProps<{
  dataFiles: string[]
  disabled?: boolean
}>()

const dataConfirmed = defineModel<boolean>('dataConfirmed', { required: true })
const inputDescription = defineModel<string>('inputDescription', { required: true })
const expectedOutput = defineModel<string>('expectedOutput', { required: true })
const description = defineModel<string>('description', { required: true })
</script>

<template>
  <div class="data">
    <div class="data__confirm" :class="{ 'is-checked': dataConfirmed }">
      <BaseCheckbox v-model="dataConfirmed" :disabled="disabled">
        {{ $t('analysis.data.confirm') }}
        <template #description>
          <template v-if="dataFiles.length">
            {{ $t('analysis.data.found') }}
            <code v-for="file in dataFiles" :key="file">{{ file }}</code>
          </template>
          <template v-else>{{ $t('analysis.data.notFound') }}</template>
        </template>
      </BaseCheckbox>
    </div>

    <div class="data__field">
      <label for="data-input" class="data__label">{{ $t('analysis.data.input') }}</label>
      <BaseTextarea
        id="data-input"
        v-model="inputDescription"
        :maxlength="8192"
        :placeholder="$t('analysis.data.inputPlaceholder')"
        :disabled="disabled"
      />
    </div>

    <div class="data__field">
      <label for="data-output" class="data__label">{{ $t('analysis.data.output') }}</label>
      <BaseTextarea
        id="data-output"
        v-model="expectedOutput"
        :maxlength="8192"
        :placeholder="$t('analysis.data.outputPlaceholder')"
        :disabled="disabled"
      />
    </div>

    <div class="data__field">
      <label for="data-description" class="data__label">{{ $t('analysis.data.description') }}</label>
      <BaseTextarea
        id="data-description"
        v-model="description"
        :rows="2"
        :maxlength="8192"
        :placeholder="$t('analysis.data.descriptionPlaceholder')"
        :disabled="disabled"
      />
    </div>

    <p class="data__note">{{ $t('analysis.data.note') }}</p>
  </div>
</template>

<style scoped>
.data {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.data__confirm {
  padding: 18px 20px;
  border: 1px dashed var(--color-border-strong);
  border-radius: var(--radius-lg);
  background: var(--color-bg);
  transition: border-color var(--transition);
}

.data__confirm.is-checked {
  border-style: solid;
  border-color: var(--color-border);
}

.data__confirm code {
  margin-left: 4px;
  padding: 1px 6px;
  border-radius: var(--radius-xs);
  background: var(--color-surface-raised);
  color: var(--color-text-secondary);
  font-family: var(--font-mono);
  font-size: 12px;
}

.data__field {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.data__label {
  font-size: 14px;
  font-weight: 500;
}

.data__note {
  color: var(--color-text-muted);
  font-size: 13px;
}
</style>
