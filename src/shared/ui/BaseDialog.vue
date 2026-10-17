<script setup lang="ts">
import { nextTick, ref, useId, watch } from 'vue'

const props = withDefaults(defineProps<{ title: string; size?: 'default' | 'wide' }>(), {
  size: 'default',
})
const open = defineModel<boolean>('open', { required: true })

const panel = ref<HTMLElement | null>(null)
const titleId = useId()
let returnFocus: HTMLElement | null = null

watch(open, async (isOpen) => {
  if (isOpen) {
    returnFocus = document.activeElement as HTMLElement | null
    await nextTick()
    panel.value?.querySelector<HTMLElement>('input, button')?.focus()
  } else {
    returnFocus?.focus()
  }
})

function close() {
  open.value = false
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') return close()
  if (event.key !== 'Tab' || !panel.value) return
  const items = [...panel.value.querySelectorAll<HTMLElement>('button, input, [href], [tabindex]:not([tabindex="-1"])')].filter(
    (el) => !el.hasAttribute('disabled'),
  )
  const first = items[0]
  const last = items[items.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last?.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first?.focus()
  }
}

defineExpose({ close, title: props.title })
</script>

<template>
  <Teleport to="body">
    <Transition name="dialog">
      <div v-if="open" class="dialog" @keydown="onKeydown" @mousedown.self="close">
        <div ref="panel" class="dialog__panel" :class="{ 'dialog__panel--wide': size === 'wide' }" role="dialog" aria-modal="true" :aria-labelledby="titleId">
          <h2 :id="titleId" class="dialog__title">{{ title }}</h2>
          <div class="dialog__body"><slot /></div>
          <div class="dialog__actions"><slot name="actions" /></div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.dialog {
  position: fixed;
  z-index: 100;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: rgb(0 0 0 / 0.72);
}

.dialog__panel {
  width: min(460px, 100%);
  padding: 28px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  background: var(--color-surface);
  box-shadow: 0 24px 64px rgb(0 0 0 / 0.6);
}

.dialog__title {
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 500;
  line-height: 1.25;
}

.dialog__panel--wide {
  width: min(1120px, 100%);
  max-height: calc(100dvh - 32px);
  overflow: auto;
}

.dialog__body {
  margin-top: 12px;
  color: var(--color-text-secondary);
  font-size: 14px;
  line-height: 1.5;
}

.dialog__actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 28px;
}

.dialog-enter-active,
.dialog-leave-active {
  transition: opacity 160ms ease;
}

.dialog-enter-active .dialog__panel,
.dialog-leave-active .dialog__panel {
  transition: transform 160ms ease;
}

.dialog-enter-from,
.dialog-leave-to {
  opacity: 0;
}

.dialog-enter-from .dialog__panel,
.dialog-leave-to .dialog__panel {
  transform: translateY(8px) scale(0.98);
}

@media (max-width: 480px) {
  .dialog__actions {
    flex-direction: column-reverse;
  }
}
</style>
