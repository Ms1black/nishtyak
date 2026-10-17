import hljs from 'highlight.js/lib/core'
import bash from 'highlight.js/lib/languages/bash'
import dockerfile from 'highlight.js/lib/languages/dockerfile'
import ini from 'highlight.js/lib/languages/ini'
import json from 'highlight.js/lib/languages/json'
import powershell from 'highlight.js/lib/languages/powershell'
import python from 'highlight.js/lib/languages/python'
import yaml from 'highlight.js/lib/languages/yaml'

hljs.registerLanguage('python', python)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('powershell', powershell)
hljs.registerLanguage('json', json)
hljs.registerLanguage('yaml', yaml)
hljs.registerLanguage('toml', ini)
hljs.registerLanguage('ini', ini)
hljs.registerLanguage('dockerfile', dockerfile)

export type CodeLanguage =
  'python' | 'bash' | 'powershell' | 'json' | 'yaml' | 'toml' | 'dockerfile' | 'ini' | 'text'
export const MAX_HIGHLIGHT_CHARACTERS = 40_000
export const MAX_HIGHLIGHT_LINES = 1000
export const MAX_PREVIEW_CHARACTERS = 200_000
export const MAX_PREVIEW_LINES = 5000

const aliases: Record<string, CodeLanguage> = {
  py: 'python',
  py3: 'python',
  python3: 'python',
  python: 'python',
  bash: 'bash',
  sh: 'bash',
  shell: 'bash',
  console: 'bash',
  zsh: 'bash',
  ps: 'powershell',
  ps1: 'powershell',
  pwsh: 'powershell',
  powershell: 'powershell',
  json: 'json',
  jsonc: 'json',
  yaml: 'yaml',
  yml: 'yaml',
  toml: 'toml',
  docker: 'dockerfile',
  dockerfile: 'dockerfile',
  ini: 'ini',
  cfg: 'ini',
  conf: 'ini',
  txt: 'text',
  text: 'text',
  plaintext: 'text',
  plain: 'text',
}

export function escapeHtml(value: string): string {
  return value.replace(
    /[&<>"']/g,
    (character) =>
      ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
      })[character]!,
  )
}

export function codeLanguage(language?: string, filename?: string): CodeLanguage {
  if (language?.trim()) return aliases[language.trim().toLowerCase()] ?? 'text'
  const name = filename?.replaceAll('\\', '/').split('/').at(-1)?.toLowerCase() ?? ''
  if (name === 'dockerfile' || name.startsWith('dockerfile.')) return 'dockerfile'
  const extension = name.split('.').at(-1) ?? ''
  return aliases[extension] ?? 'text'
}

export interface HighlightedCode {
  html: string
  text: string
  language: CodeLanguage
  highlighted: boolean
  truncated: boolean
  lineCount: number
}

export function highlightCode(code: string, language?: string, filename?: string): HighlightedCode {
  const selected = codeLanguage(language, filename)
  const prefix = code.slice(0, MAX_PREVIEW_CHARACTERS)
  const lines = prefix.split('\n')
  const text = lines.slice(0, MAX_PREVIEW_LINES).join('\n')
  const truncated = text.length < code.length
  const result: HighlightedCode = {
    html: escapeHtml(text),
    text,
    language: selected,
    highlighted: false,
    truncated,
    lineCount: text.split('\n').length,
  }
  if (
    selected === 'text' ||
    text.length > MAX_HIGHLIGHT_CHARACTERS ||
    lines.length > MAX_HIGHLIGHT_LINES ||
    lines.some((line) => line.length > 2000)
  )
    return result
  try {
    result.html = hljs.highlight(text, { language: selected, ignoreIllegals: true }).value
    result.highlighted = true
  } catch {
    // A grammar failure must not prevent viewing or copying the original source.
  }
  return result
}
