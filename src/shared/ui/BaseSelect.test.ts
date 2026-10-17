// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import BaseSelect from './BaseSelect.vue'

let application: App | undefined
let container: HTMLElement | undefined

function mountSelect(top: number, bottom: number, viewportHeight = 720) {
  vi.stubGlobal('innerHeight', viewportHeight)
  container = document.createElement('div')
  document.body.appendChild(container)
  const update = vi.fn()
  application = createApp({
    render: () => h(BaseSelect, {
      modelValue: 'member',
      options: [
        { value: 'member', label: 'Участник' },
        { value: 'admin', label: 'Администратор' },
        { value: 'viewer', label: 'Наблюдатель' },
      ],
      ariaLabel: 'Роль участника',
      'onUpdate:modelValue': update,
    }),
  })
  application.mount(container)
  const root = container.querySelector<HTMLElement>('.select')!
  const button = container.querySelector<HTMLButtonElement>('[role="combobox"]')!
  const list = container.querySelector<HTMLElement>('[role="listbox"]')!
  const bounds = { top, bottom }
  vi.spyOn(root, 'getBoundingClientRect').mockImplementation(() => bounds as DOMRect)
  Object.defineProperty(list, 'scrollHeight', { configurable: true, value: 260 })
  return { root, button, list, bounds, update }
}

async function flushLayout() {
  await nextTick()
  await nextTick()
  await nextTick()
}

afterEach(() => {
  application?.unmount()
  container?.remove()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('select popup placement', () => {
  it('opens above a last-row trigger so all three roles remain available', async () => {
    const { root, button, list } = mountSelect(600, 640)
    button.click()
    await flushLayout()
    expect(root.classList.contains('select--up')).toBe(true)
    expect(list.style.maxHeight).toBe('580px')
    expect(list.querySelectorAll('[role="option"]')).toHaveLength(3)
    expect(button.getAttribute('aria-expanded')).toBe('true')
  })

  it('updates placement after viewport resize and ancestor scrolling', async () => {
    const { root, button, list, bounds } = mountSelect(600, 640)
    button.click()
    await flushLayout()
    vi.stubGlobal('innerHeight', 1100)
    window.dispatchEvent(new Event('resize'))
    await flushLayout()
    expect(root.classList.contains('select--up')).toBe(false)
    expect(list.style.maxHeight).toBe('440px')

    bounds.top = 1000
    bounds.bottom = 1040
    container!.dispatchEvent(new Event('scroll'))
    await flushLayout()
    expect(root.classList.contains('select--up')).toBe(true)
  })

  it('limits a tall menu to available space and scrolls keyboard choices into view', async () => {
    const { button, list, update } = mountSelect(80, 120, 200)
    Object.defineProperty(list, 'clientHeight', { configurable: true, value: 60 })
    const options = [...list.children] as HTMLElement[]
    options.forEach((option, index) => {
      Object.defineProperties(option, {
        offsetTop: { configurable: true, value: index * 40 },
        offsetHeight: { configurable: true, value: 40 },
      })
    })
    button.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))
    await flushLayout()
    expect(list.style.maxHeight).toBe('60px')
    button.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))
    button.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))
    await flushLayout()
    expect(list.scrollTop).toBe(60)
    expect(button.getAttribute('aria-activedescendant')).toBe(options[2]!.id)
    button.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    await flushLayout()
    expect(update).toHaveBeenCalledWith('viewer')
    expect(button.getAttribute('aria-expanded')).toBe('false')
  })
})
