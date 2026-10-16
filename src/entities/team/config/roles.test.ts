import { describe, expect, it, vi } from 'vitest'
import { canManageMember, canManageMembers, getTeamRoleOptions } from './roles'
vi.mock('@/shared/i18n', () => ({ t: (key: string) => key }))

describe('team management permissions', () => {
  it('permits administrators to manage only regular members and viewers', () => {
    expect(canManageMember('admin', 'member', false)).toBe(true)
    expect(canManageMember('admin', 'viewer', false)).toBe(true)
    expect(canManageMember('admin', 'admin', false)).toBe(false)
    expect(canManageMember('admin', 'owner', false)).toBe(false)
    expect(getTeamRoleOptions('admin').map((option) => option.value)).toEqual(['member', 'viewer'])
  })
  it('preserves ownership and prevents changing yourself', () => {
    expect(canManageMember('owner', 'admin', false)).toBe(true)
    expect(canManageMember('owner', 'owner', false)).toBe(false)
    expect(canManageMember('owner', 'member', true)).toBe(false)
    expect(canManageMembers('viewer')).toBe(false)
    expect(canManageMember('member', 'viewer', false)).toBe(false)
  })
})
