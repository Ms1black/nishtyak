import { t } from '@/shared/i18n'
import type { IconName, SelectOption } from '@/shared/ui'
import type { TeamMemberRole, TeamRole } from '../model/types'

const ROLE_ICONS: Record<TeamRole, IconName> = { member: 'user', admin: 'shield', viewer: 'eye' }

export function getTeamRoleOptions(): SelectOption<TeamRole>[] {
  return (['member', 'admin', 'viewer'] as const).map((value) => ({
    value,
    label: t(`roles.${value}`),
    description: t(`roles.description.${value}`),
    icon: ROLE_ICONS[value],
  }))
}

export function getRoleLabel(role: TeamMemberRole): string {
  return t(`roles.${role}`)
}

export function canManageMembers(actor: TeamMemberRole): boolean {
  return actor === 'owner' || actor === 'admin'
}
