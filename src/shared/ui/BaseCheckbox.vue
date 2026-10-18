<script setup lang="ts">
import BaseIcon from './BaseIcon.vue'

defineProps<{ disabled?: boolean }>()

const model = defineModel<boolean>({ required: true })
</script>

<template>
  <label class="checkbox" :class="{ 'is-checked': model, 'is-disabled': disabled }">
    <input v-model="model" type="checkbox" class="visually-hidden" :disabled="disabled" />
    <span class="checkbox__box" aria-hidden="true">
      <BaseIcon v-if="model" name="check" :size="14" :stroke-width="2.6" />
    </span>
    <span class="checkbox__text">
      <span class="checkbox__label"><slot /></span>
      <span v-if="$slots.description" class="checkbox__description"><slot name="description" /></span>
    </span>
  </label>
</template>

<style scoped>
.checkbox {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  cursor: pointer;
}

.checkbox.is-disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.checkbox:has(:focus-visible) .checkbox__box {
  outline: 2px solid var(--color-focus);
  outline-offset: 2px;
}

.checkbox__box {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  margin-top: 1px;
  border: 1.5px solid var(--color-border-strong);
  border-radius: var(--radius-xs);
  transition:
    background-color var(--transition),
    border-color var(--transition);
}

.checkbox.is-checked .checkbox__box {
  border-color: var(--color-inverse-bg);
  background: var(--color-inverse-bg);
  color: var(--color-inverse-text);
}

.checkbox__text {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.checkbox__label {
  font-size: 15px;
  font-weight: 500;
}

.checkbox__description {
  color: var(--color-text-muted);
  font-size: 13px;
}
</style>
