import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useInviteStore } from './inviteStore'

const { mode, listInvites, createInvite } = vi.hoisted(() => ({
  mode: { standalone: true },
  listInvites: vi.fn(),
  createInvite: vi.fn(),
}))

vi.mock('@/entities/team', () => ({
  useTeamStore: () => ({ currentTeamId: 'workspace' }),
  teamApi: { listInvites, createInvite },
}))
vi.mock('@/shared/api', () => ({ isStandaloneWorkspace: { get value() { return mode.standalone } } }))
vi.mock('@/shared/i18n', () => ({ t: (key: string) => key }))

describe('shared workspace invitation preview', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    mode.standalone = true
    listInvites.mockResolvedValue([])
    setActivePinia(createPinia())
  })

  it('loads an empty real invitation list without creating an invitation as a side effect', async () => {
    const store = useInviteStore()
    await vi.waitFor(() => expect(store.isLoading).toBe(false))
    expect(listInvites).toHaveBeenCalledWith('workspace')
    expect(createInvite).not.toHaveBeenCalled()
    expect(store.current).toBeNull()
    expect(store.error).toBeNull()
  })

  it('does not submit unsupported invitation mutations from the shared workspace', async () => {
    const store = useInviteStore()
    await store.add('member')
    await store.rotate()
    expect(createInvite).not.toHaveBeenCalled()
    expect(store.invites).toEqual([])
  })
})
