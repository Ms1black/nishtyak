import { beforeEach, describe, expect, it, vi } from 'vitest'
import { projectHttpApi } from './projectApi.http'

const { get } = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@/shared/api', () => ({ apiClient: { get } }))

describe('built archive file API', () => {
  beforeEach(() => { vi.resetAllMocks() })

  it.each(['README.md', 'analysis-report.json', 'project/lab experiment.py', 'project/data/A+B & Ω.csv'])('requests the exact ZIP member %s and archive SHA', async (path) => {
    const archiveTag = 'a'.repeat(64)
    const content = { kind: 'text', content: 'Original file\r\n' }
    get.mockResolvedValueOnce(content)
    expect(await projectHttpApi.getArchiveFileContent('project', path, archiveTag)).toBe(content)
    expect(get).toHaveBeenCalledExactlyOnceWith(`/projects/project/archive/files?path=${encodeURIComponent(path)}&archiveTag=${archiveTag}`)
    const url = new URL(get.mock.calls[0]![0], 'https://test.invalid')
    expect(url.pathname).toBe('/projects/project/archive/files')
    expect(url.searchParams.get('path')).toBe(path)
    expect(url.searchParams.get('archiveTag')).toBe(archiveTag)
  })
})
