import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from './client'
import { getIntegrationSession, integrationSession, resetIntegrationSession } from './session'

const identity = (id: string) => ({ mode: 'accounts' as const, user: { id, name: id, email: `${id}@example.org` }, team: { id: `team-${id}`, name: id } })

describe('account session cache', () => {
  beforeEach(() => { vi.restoreAllMocks(); resetIntegrationSession() })

  it('reloads the session after login/logout instead of reusing another identity', async () => {
    const get = vi.spyOn(apiClient, 'get').mockResolvedValueOnce(identity('alice')).mockResolvedValueOnce(identity('bob'))
    await getIntegrationSession()
    resetIntegrationSession()
    expect(integrationSession.value).toBeNull()
    await getIntegrationSession()
    expect(integrationSession.value?.user.id).toBe('bob')
    expect(get).toHaveBeenCalledTimes(2)
  })

  it('discards an older request that completes after the session is reset', async () => {
    let resolve!: (value: ReturnType<typeof identity>) => void
    vi.spyOn(apiClient, 'get').mockImplementationOnce(() => new Promise((done) => { resolve = done })).mockResolvedValueOnce(identity('bob'))
    const old = getIntegrationSession()
    resetIntegrationSession()
    await getIntegrationSession()
    resolve(identity('alice'))
    expect(await old).toBeNull()
    expect(integrationSession.value?.user.id).toBe('bob')
  })
})
