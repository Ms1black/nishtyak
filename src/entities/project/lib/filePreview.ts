const EXTENSIONS: Record<string, string> = {
  py: 'python', pyi: 'python', json: 'json', ipynb: 'json',
  yaml: 'yaml', yml: 'yaml', toml: 'toml',
  sh: 'bash', bash: 'bash', zsh: 'bash', ps1: 'powershell', bat: 'bat', cmd: 'bat',
  ini: 'ini', cfg: 'ini', env: 'ini',
  md: 'markdown', markdown: 'markdown',
  html: 'xml', htm: 'xml', xml: 'xml', sql: 'sql',
  js: 'javascript', mjs: 'javascript', ts: 'typescript', css: 'css',
}

export function isMarkdownFile(path: string): boolean {
  return /\.(?:md|markdown)$/i.test(path)
}

export function getFileLanguage(path: string): string {
  const name = path.split('/').at(-1)?.toLowerCase() ?? ''
  if (/^dockerfile(?:\.|$)/.test(name)) return 'dockerfile'
  if (name === 'uv.lock') return 'toml'
  if (name === '.env') return 'ini'
  return EXTENSIONS[name.split('.').at(-1) ?? ''] ?? 'plaintext'
}
