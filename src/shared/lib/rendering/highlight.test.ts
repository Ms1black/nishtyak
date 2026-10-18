import { describe, expect, it } from 'vitest'
import {
  codeLanguage,
  escapeHtml,
  highlightCode,
  MAX_HIGHLIGHT_CHARACTERS,
  MAX_PREVIEW_CHARACTERS,
  MAX_PREVIEW_LINES,
} from './highlight'

describe('explicit bounded syntax highlighting', () => {
  it.each([
    ['python', 'import numpy as np\ndef compute():\n    return 42', 'hljs-keyword'],
    ['bash', 'python -m pip install -r requirements.txt\necho "ready"', 'hljs-string'],
    ['powershell', '$env:PATH = "value"\nWrite-Output $env:PATH', 'hljs'],
    ['json', '{"enabled": true, "count": 2}', 'hljs-attr'],
    ['yaml', 'dependencies:\n  - numpy\nenabled: true', 'hljs-attr'],
    ['toml', '[project]\nname = "experiment"', 'hljs-section'],
    ['ini', '[service]\nport = 8080', 'hljs-section'],
    ['dockerfile', 'FROM python:3.11-slim\nRUN echo ready', 'hljs-keyword'],
  ])('highlights only the requested %s grammar', (language, source, token) => {
    const result = highlightCode(source, language)
    expect(result.highlighted).toBe(true)
    expect(result.html).toContain(token)
    expect(result.text).toBe(source)
  })

  it('uses explicit language before filename, infers known extensions and never autodetects', () => {
    expect(codeLanguage(undefined, 'src/experiment.py')).toBe('python')
    expect(codeLanguage(undefined, 'Dockerfile')).toBe('dockerfile')
    expect(codeLanguage('plaintext', 'main.py')).toBe('text')
    expect(codeLanguage('sh')).toBe('bash')
    expect(highlightCode('import os', undefined).highlighted).toBe(false)
    expect(highlightCode('import os', 'not-a-grammar').language).toBe('text')
  })

  it('escapes tags and does not interpolate untrusted language names', () => {
    const result = highlightCode('<img src=x onerror=alert(1)>', 'python" onmouseover="alert(1)')
    expect(result.language).toBe('text')
    expect(result.html).toBe('&lt;img src=x onerror=alert(1)&gt;')
    expect(escapeHtml('<script>"&\'')).toBe('&lt;script&gt;&quot;&amp;&#39;')
  })

  it('shows large source as escaped plain text without calling a grammar', () => {
    const result = highlightCode(
      'x = 1\n'.repeat(Math.ceil(MAX_HIGHLIGHT_CHARACTERS / 6)),
      'python',
    )
    expect(result.highlighted).toBe(false)
    expect(result.html).not.toContain('<span')
    expect(result.text).toContain('x = 1')
    const longLine = highlightCode('x'.repeat(2001), 'python')
    expect(longLine.highlighted).toBe(false)
  })

  it('bounds preview characters and line DOM independently of the source copied by consumers', () => {
    const characters = highlightCode('x'.repeat(MAX_PREVIEW_CHARACTERS + 50), 'text')
    expect(characters.text).toHaveLength(MAX_PREVIEW_CHARACTERS)
    expect(characters.truncated).toBe(true)
    const lines = highlightCode('x\n'.repeat(MAX_PREVIEW_LINES + 10), 'python')
    expect(lines.lineCount).toBe(MAX_PREVIEW_LINES)
    expect(lines.truncated).toBe(true)
  })
})
