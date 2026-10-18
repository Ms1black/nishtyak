import { describe, expect, it } from 'vitest'
import { ACCEPTED_EXTENSIONS, MAX_FILE_SIZE, validateProjectFile } from './validateProjectFile'

describe('ZIP project intake', () => {
  it('accepts one nonempty ZIP without exposing direct Python uploads in the UI', () => {
    expect(ACCEPTED_EXTENSIONS).toEqual(['.zip'])
    expect(validateProjectFile(new File(['archive'], 'project.ZIP'))).toBeNull()
    expect(validateProjectFile(new File(['print(1)'], 'experiment.py'))).toBe('upload.errors.type')
  })

  it('rejects empty, oversized, and unsupported files', () => {
    expect(validateProjectFile(new File([], 'project.zip'))).toBe('upload.errors.empty')
    expect(validateProjectFile(new File(['text'], 'project.txt'))).toBe('upload.errors.type')
    expect(validateProjectFile(new File([new Uint8Array(MAX_FILE_SIZE + 1)], 'project.zip'))).toBe('upload.errors.size')
  })
})
