<script setup lang="ts" generic="T extends string">
import { computed, ref, useId } from 'vue'
import BaseIcon from './BaseIcon.vue'
import type { IconName } from './icons'
import type { SelectOption } from './types'
import { useClickOutside } from '../lib/useClickOutside'

const props = defineProps<{
  options: SelectOption<T>[]
  placeholder?: string
  icon?: IconName
  disabled?: boolean
  ariaLabelledby?: string
  ariaLabel?: string
  size?: 'md' | 'sm'
}>()

const model = defineModel<T | null>({ required: true })

const root = ref<HTMLElement | null>(null)
const isOpen = ref(false)
const activeIndex = ref(-1)
const listId = useId()

const selected = computed(() => props.options.find((o) => o.value === model.value) ?? null)
const triggerIcon = computed(() => selected.value?.icon ?? props.icon)

useClickOutside(root, () => close())

function open() {
  if (props.disabled) return
  isOpen.value = true
  activeIndex.value = Math.max(
    0,
    props.options.findIndex((o) => o.value === model.value),
  )
}

function close() {
  isOpen.value = false
}

function toggle() {
  if (isOpen.value) close()
  else open()
}

function choose(option: SelectOption<T>) {
  model.value = option.value
  close()
}

function onKeydown(event: KeyboardEvent) {
  const total = props.options.length
  if (!isOpen.value) {
    if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(event.key)) {
      event.preventDefault()
      open()
    }
    return
  }
  switch (event.key) {
    case 'Escape':
      close()
      break
    case 'Tab':
      close()
      break
    case 'ArrowDown':
      event.preventDefault()
      activeIndex.value = (activeIndex.value + 1) % total
      break
    case 'ArrowUp':
      event.preventDefault()
      activeIndex.value = (activeIndex.value - 1 + total) % total
      break
    case 'Enter':
    case ' ': {
      event.preventDefault()
      const option = props.options[activeIndex.value]
      if (option) choose(option)
      break
    }
  }
}
</script>

<template>
  <div ref="root" class="select" :class="[`select--${size ?? 'md'}`, { 'is-open': isOpen, 'is-disabled': disabled }]">
    <button
      type="button"
      class="select__trigger"
      role="combobox"
      aria-haspopup="listbox"
      :aria-expanded="isOpen"
      :aria-controls="listId"
      :aria-labelledby="ariaLabelledby"
      :aria-label="ariaLabel"
      :aria-activedescendant="isOpen && activeIndex >= 0 ? `${listId}-${activeIndex}` : undefined"
      :disabled="disabled"
      @click="toggle"
      @keydown="onKeydown"
    >
      <BaseIcon v-if="triggerIcon" :name="triggerIcon" :size="22" class="select__icon" />
      <span class="select__value" :class="{ 'is-placeholder': !selected }">
        {{ selected?.label ?? placeholder ?? $t('common.select') }}
      </span>
      <BaseIcon name="chevron-down" :size="18" class="select__chevron" />
    </button>

    <Transition name="select-pop">
      <ul v-show="isOpen" :id="listId" class="select__list" role="listbox">
        <li
          v-for="(option, index) in options"
          :id="`${listId}-${index}`"
          :key="option.value"
          class="select__option"
          :class="{ 'is-active': index === activeIndex, 'is-selected': option.value === model }"
          role="option"
          :aria-selected="option.value === model"
          @click="choose(option)"
          @mouseenter="activeIndex = index"
        >
          <BaseIcon v-if="option.icon" :name="option.icon" :size="20" class="select__option-icon" />
          <span class="select__option-text">
            <span class="select__option-label">{{ option.label }}</span>
            <span v-if="option.description" class="select__option-desc">{{ option.description }}</span>
          </span>
          <BaseIcon v-if="option.value === model" name="check" :size="18" class="select__check" />
        </li>
      </ul>
    </Transition>
  </div>
</template>

<style scoped>
.select {
  position: relative;
}

.select__trigger {
  display: flex;
  align-items: center;
  gap: 14px;
  width: 100%;
  height: 52px;
  padding: 0 20px 0 18px;
  border-radius: var(--radius-md);
  background: var(--color-bg);
  text-align: left;
  font-size: 16px;
  transition: background-color var(--transition);
}

.select--sm .select__trigger {
  gap: 10px;
  height: 40px;
  padding: 0 12px 0 14px;
  border-radius: 10px;
  font-size: 14px;
}

.select--sm .select__list {
  min-width: 280px;
  left: auto;
}

.select__trigger:hover:not(:disabled) {
  background: #0a0a0a;
}

.select__icon {
  color: var(--color-text-secondary);
}

.select__value {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.select__value.is-placeholder {
  color: var(--color-text-muted);
}

.select__chevron {
  color: var(--color-text-secondary);
  transition: transform var(--transition);
}

.is-open .select__chevron {
  transform: rotate(180deg);
}

.is-disabled {
  opacity: 0.5;
}

.select__list {
  position: absolute;
  z-index: 20;
  top: calc(100% + 8px);
  left: 0;
  right: 0;
  padding: 6px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-raised);
  box-shadow: 0 16px 40px rgb(0 0 0 / 0.5);
}

.select__option {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  cursor: pointer;
}

.select__option.is-active {
  background: #303030;
}

.select__option-icon {
  color: var(--color-text-secondary);
}

.select__option-text {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
}

.select__option-label {
  font-size: 15px;
}

.select__option-desc {
  color: var(--color-text-muted);
  font-size: 13px;
}

.select__check {
  color: var(--color-text);
}

.select-pop-enter-active,
.select-pop-leave-active {
  transition:
    opacity var(--transition),
    transform var(--transition);
}

.select-pop-enter-from,
.select-pop-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
