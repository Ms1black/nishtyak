import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import type { VerificationDocument } from '@/entities/project/model/types'
import { ApiError } from '@/shared/api'
import { ArchiveReview, readmeCommands } from './archiveReview'

vi.mock('@/entities/project', () => ({ projectApi: {} }))

const document = (): VerificationDocument => ({
  revision: 0, analysisId: 'analysis', archiveTag: 'sha-current', stale: false, verification: null,
})

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

describe('archive README and manual verification', () => {
  const api = { getReadme: vi.fn(), getVerification: vi.fn(), saveVerification: vi.fn() }

  beforeEach(() => {
    vi.clearAllMocks()
    api.getReadme.mockResolvedValue({ content: '# README\n```sh\npython experiment.py\n```' })
    api.getVerification.mockResolvedValue(document())
    api.saveVerification.mockResolvedValue({ ...document(), revision: 1 })
  })

  it('copies only actual shell blocks and retains their commands without inventing a main file', () => {
    const content = '# Проект\n## Установка\n```bash\npython -m venv .venv\npython -m pip install -r requirements.txt\n```\n## Запуск\n```sh\npython -m measurement --input data.csv\n```\n```python\nraise RuntimeError()\n```\n<script>ignored()</script>'
    expect(readmeCommands(content)).toEqual([
      { title: 'Установка', content: 'python -m venv .venv\npython -m pip install -r requirements.txt' },
      { title: 'Запуск', content: 'python -m measurement --input data.csv' },
    ])
  })

  it('keeps verification available when README temporarily fails, and retries only README', async () => {
    api.getReadme.mockRejectedValueOnce(new ApiError(503, 'Unavailable'))
    const review = new ArchiveReview(api)
    await review.open('project', 'sha-current')
    expect(review.state.readmeError).toBe('ready.readme.error')
    expect(review.state.verification).toEqual(document())
    await review.loadReadme()
    expect(review.state.readmeError).toBeNull()
    expect(review.state.readme).toContain('python experiment.py')
    expect(api.getVerification).toHaveBeenCalledOnce()
  })

  it('keeps README available and verification retriable when its GET fails', async () => {
    api.getVerification.mockRejectedValueOnce(new ApiError(503, 'Unavailable'))
    const review = new ArchiveReview(api)
    await review.open('project')
    expect(review.state.readme).toContain('python experiment.py')
    expect(review.state.verificationError).toBe('ready.verification.loadError')
    await review.loadVerification()
    expect(review.state.verificationError).toBeNull()
    expect(review.state.verification).toEqual(document())
  })

  it('records a human result against the exact analysis, archive hash and verification revision', async () => {
    const review = new ArchiveReview(api)
    await review.open('project', 'sha-current')
    Object.assign(review.state.form, { status: 'passed', pythonVersion: '3.12', platform: 'Linux', command: 'python experiment.py', output: '2 measurements', notes: 'Checked in a fresh venv.' })
    await review.save(true)
    expect(api.saveVerification).toHaveBeenCalledExactlyOnceWith('project', {
      expectedRevision: 0, analysisId: 'analysis', archiveTag: 'sha-current',
      status: 'passed', pythonVersion: '3.12', platform: 'Linux', command: 'python experiment.py',
      output: '2 measurements', notes: 'Checked in a fresh venv.',
    })
    expect(review.state.saved).toBe(true)
    await nextTick()
    expect(review.state.saved).toBe(true)
    review.state.form.notes = 'Changed after saving'
    expect(review.state.saved).toBe(false)
  })

  it('does not save verification for viewers', async () => {
    const review = new ArchiveReview(api)
    await review.open('project', 'sha-current')
    await review.save(false)
    expect(api.saveVerification).not.toHaveBeenCalled()
  })

  it('keeps entered text after save 503 and retries the same submission', async () => {
    const review = new ArchiveReview(api)
    await review.open('project', 'sha-current')
    Object.assign(review.state.form, { status: 'failed', output: 'Missing input.csv', notes: 'Need the synthetic dataset.' })
    api.saveVerification.mockRejectedValueOnce(new ApiError(503, 'Unavailable'))
    await review.save(true)
    expect(review.state.verificationError).toBe('ready.verification.saveError')
    expect(review.state.form.output).toBe('Missing input.csv')
    expect(review.state.form.notes).toBe('Need the synthetic dataset.')
    await review.save(true)
    expect(api.saveVerification).toHaveBeenCalledTimes(2)
    expect(api.saveVerification.mock.calls[1]?.[1].notes).toBe('Need the synthetic dataset.')
    expect(api.getVerification).toHaveBeenCalledOnce()
  })

  it('marks conflicts without silently applying a result to another archive', async () => {
    const review = new ArchiveReview(api)
    await review.open('project', 'sha-old')
    await review.save(true)
    expect(api.saveVerification).not.toHaveBeenCalled()
    expect(review.state.verificationConflict).toBe(true)
    expect(review.state.verificationError).toBe('ready.verification.conflict')
    await review.open('project', 'sha-current')
    await review.save(true)
    expect(api.saveVerification).toHaveBeenCalledOnce()
  })

  it('ignores stale README and verification responses after switching project', async () => {
    const oldReadme = deferred<{ content: string }>()
    const oldVerification = deferred<VerificationDocument>()
    api.getReadme.mockReturnValueOnce(oldReadme.promise)
    api.getVerification.mockReturnValueOnce(oldVerification.promise)
    const review = new ArchiveReview(api)
    const old = review.open('old', 'sha-old')
    await review.open('new', 'sha-current')
    oldReadme.resolve({ content: 'Private old README' })
    oldVerification.resolve({ ...document(), analysisId: 'old-analysis' })
    await old
    expect(review.state.readme).not.toContain('Private old README')
    expect(review.state.verification?.analysisId).toBe('analysis')
  })

  it('discards an old save response after reset or logout', async () => {
    const saved = deferred<VerificationDocument>()
    api.saveVerification.mockReturnValueOnce(saved.promise)
    const review = new ArchiveReview(api)
    await review.open('private', 'sha-current')
    const saving = review.save(true)
    review.reset()
    saved.resolve({ ...document(), revision: 1 })
    await saving
    expect(review.state.verification).toBeNull()
    expect(review.state.saved).toBe(false)
    expect(review.state.form.notes).toBe('')
  })
})
