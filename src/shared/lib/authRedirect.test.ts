import { describe, expect, it } from 'vitest'
import { safeAuthRedirect } from './authRedirect'

describe('authentication return paths', () => {
  it('preserves the invitation and project return routes', () => {
    expect(safeAuthRedirect('/invite/team')).toBe('/invite/team')
    expect(safeAuthRedirect('/upload?project=abc')).toBe('/upload?project=abc')
  })
  it.each(['//evil.example', '/\\evil.example', 'https://evil.example', '/login', '/register?redirect=/upload', '/\nevil.example', null, ['upload']])('rejects unsafe or looping redirect %j', (input) => {
    expect(safeAuthRedirect(input)).toBe('/upload')
  })
})
