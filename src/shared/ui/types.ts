import type { RouteLocationRaw } from 'vue-router'
import type { IconName } from './icons'

export interface SegmentedItem<V extends string | number = string> {
  value: V
  label: string
  prefix?: string
  to?: RouteLocationRaw
  disabled?: boolean
}

export interface SelectOption<V extends string = string> {
  value: V
  label: string
  description?: string
  icon?: IconName
}
