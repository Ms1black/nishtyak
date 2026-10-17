import { computed, watch } from 'vue'
import { createI18n } from 'vue-i18n'
import { en } from './locales/en'
import { ru, type MessageSchema } from './locales/ru'

export type Locale = 'ru' | 'en'

export const LOCALES: { value: Locale; label: string; short: string }[] = [
  { value: 'ru', label: 'Русский', short: 'RU' },
  { value: 'en', label: 'English', short: 'EN' },
]

const STORAGE_KEY = 'nishtyak:locale'

function detectLocale(): Locale {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'ru' || saved === 'en') return saved
  } catch {
  }
  return typeof navigator === 'undefined' || navigator.language.toLowerCase().startsWith('ru') ? 'ru' : 'en'
}

const ruPlural = new Intl.PluralRules('ru-RU')
function russianPluralRule(choice: number): number {
  const rule = ruPlural.select(choice)
  return rule === 'one' ? 0 : rule === 'few' ? 1 : 2
}

export const i18n = createI18n<[MessageSchema], Locale, false>({
  legacy: false,
  globalInjection: true,
  locale: detectLocale(),
  fallbackLocale: 'ru',
  messages: { ru, en },
  pluralRules: { ru: russianPluralRule },
})

export const t = i18n.global.t

export const locale = computed<Locale>({
  get: () => i18n.global.locale.value,
  set: (value) => {
    i18n.global.locale.value = value
  },
})

export const intlLocale = computed(() => (locale.value === 'ru' ? 'ru-RU' : 'en-US'))

watch(
  locale,
  (value) => {
    if (typeof document !== 'undefined') document.documentElement.lang = value
    try {
      localStorage.setItem(STORAGE_KEY, value)
    } catch {
    }
  },
  { immediate: true },
)
