import { onBeforeUnmount, onMounted, type Ref } from 'vue'

export function useClickOutside(target: Ref<HTMLElement | null>, handler: (event: PointerEvent) => void) {
  const listener = (event: PointerEvent) => {
    const el = target.value
    if (el && !el.contains(event.target as Node)) handler(event)
  }

  onMounted(() => document.addEventListener('pointerdown', listener))
  onBeforeUnmount(() => document.removeEventListener('pointerdown', listener))
}
