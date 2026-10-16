<script setup lang="ts">
defineProps<{
  placeholder?: string
  rows?: number
  maxlength?: number
  disabled?: boolean
  id?: string
}>()

const model = defineModel<string>({ required: true })
</script>

<template>
  <textarea
    :id="id"
    v-model="model"
    class="textarea"
    :rows="rows ?? 3"
    :maxlength="maxlength"
    :placeholder="placeholder"
    :disabled="disabled"
  />
</template>

<style scoped>
.textarea {
  display: block;
  width: 100%;
  min-height: 84px;
  padding: 12px 14px;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  outline: none;
  background: var(--color-bg);
  color: var(--color-text);
  font: inherit;
  font-size: 14px;
  line-height: 1.5;
  resize: vertical;
  transition: border-color var(--transition);
}

.textarea::placeholder {
  color: var(--color-text-subtle);
}

.textarea:hover:not(:disabled) {
  border-color: var(--color-border);
}

.textarea:focus {
  border-color: var(--color-text-muted);
}
</style>
