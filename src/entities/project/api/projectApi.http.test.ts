import { beforeEach, describe, expect, it, vi } from 'vitest'
import { projectHttpApi } from './projectApi.http'

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('@/shared/api', () => ({ apiClient: { get, post } }))

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

describe('target environment API', () => {
  it('submits Python and OS with the decision revision in a single request', async () => {
    post.mockResolvedValueOnce({ id: 'project', analysis: 'analyzing' })
    const target = { pythonVersion: '3.12', platform: 'windows-x86_64' as const }
    await projectHttpApi.applyTarget('project', target, 7)
    expect(post).toHaveBeenCalledExactlyOnceWith('/projects/project/target', { expectedRevision: 7, target })
  })
})
