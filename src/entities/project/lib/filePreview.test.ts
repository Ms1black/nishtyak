import { describe, expect, it } from 'vitest'
import { getFileLanguage, isMarkdownFile } from './filePreview'

describe('file preview language selection', () => {
  it.each([
    ['project/experiment.py', 'python'], ['analysis-report.json', 'json'],
    ['project/environment.yml', 'yaml'], ['project/pyproject.toml', 'toml'],
    ['Dockerfile', 'dockerfile'], ['project/Dockerfile.dev', 'dockerfile'],
    ['project/.env', 'ini'], ['project/uv.lock', 'toml'],
    ['README.md', 'markdown'], ['project/RUN.PS1', 'powershell'],
    ['requirements.lock.txt', 'plaintext'], ['project/measurements.csv', 'plaintext'],
    ['project/unknown.file', 'plaintext'],
  ])('uses %s as %s without inferring a language from its contents', (path, language) => {
    expect(getFileLanguage(path)).toBe(language)
  })

  it('recognizes Markdown case-insensitively and keeps other source files out of its renderer', () => {
    expect(isMarkdownFile('project/README.MD')).toBe(true)
    expect(isMarkdownFile('notes.markdown')).toBe(true)
    expect(isMarkdownFile('project/README.py')).toBe(false)
    expect(isMarkdownFile('requirements.txt')).toBe(false)
  })
})
