import { intlLocale, t } from '@/shared/i18n'

export function formatBytes(bytes: number): string {
  const kb = bytes / 1024
  if (kb < 1024) return t('units.kb', { n: Math.max(1, Math.round(kb)) })
  const mb = (kb / 1024).toLocaleString(intlLocale.value, { maximumFractionDigits: 1 })
  return t('units.mb', { n: mb })
}

const DAY_MS = 24 * 60 * 60 * 1000

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()
}

export function formatTime(date: Date): string {
  return date.toLocaleTimeString(intlLocale.value, { hour: '2-digit', minute: '2-digit' })
}

export function formatDayMonth(iso: string): string {
  return new Date(iso).toLocaleDateString(intlLocale.value, { day: 'numeric', month: 'long' })
}

export function formatRelativeDate(iso: string, now: Date = new Date()): string {
  const date = new Date(iso)
  const time = formatTime(date)
  const daysAgo = Math.round((startOfDay(now) - startOfDay(date)) / DAY_MS)

  if (daysAgo === 0) return t('date.today', { time })
  if (daysAgo === 1) return t('date.yesterday', { time })

  const sameYear = date.getFullYear() === now.getFullYear()
  const day = date.toLocaleDateString(intlLocale.value, {
    day: 'numeric',
    month: 'long',
    ...(sameYear ? {} : { year: 'numeric' }),
  })
  return t('date.at', { date: day, time })
}
