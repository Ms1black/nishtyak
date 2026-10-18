<script setup lang="ts">
withDefaults(
  defineProps<{
    placeholder?: string
    mono?: boolean
    attention?: boolean
    disabled?: boolean
    id?: string
    ariaLabel?: string
    type?: string
    autocomplete?: string
  }>(),
  { mono: false, attention: false, type: 'text', autocomplete: 'off' },
)

const model = defineModel<string>({ required: true })
</script>

<template>
  <input
    :id="id"
    v-model.trim="model"
    class="input"
    :class="{ 'input--mono': mono, 'input--attention': attention }"
    :placeholder="placeholder"
    :disabled="disabled"
    :aria-label="ariaLabel"
    :type="type"
    spellcheck="false"
    :autocomplete="autocomplete"
  />
</template>

<style scoped>
.input {
  width: 100%;
  height: 42px;
  padding: 0 14px;
  border: 1px solid transparent;
  border-radius: 10px;
  outline: none;
  background: var(--color-bg);
  font-size: 14px;
  transition: border-color var(--transition);
}

.input::placeholder {
  color: var(--color-text-subtle);
}

.input:hover:not(:disabled) {
  border-color: var(--color-border);
}

.input:focus {
  border-color: var(--color-text-muted);
}

.input--mono {
  font-family: var(--font-mono);
  font-size: 13px;
}

.input--attention {
  border-style: dashed;
  border-color: var(--color-border-strong);
}

.input:disabled {
  opacity: 0.5;
}
</style>
