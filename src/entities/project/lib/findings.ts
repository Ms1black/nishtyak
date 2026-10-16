import { t } from '@/shared/i18n'
import type { Finding } from '../model/types'

export type AnalysisSection = 'files' | 'dependencies' | 'run' | 'data' | 'notes'

export type FindingKind = 'blocker' | 'question' | 'limitation' | 'note'

const QUESTION_CODES = new Set(['DEPENDENCY_UNRESOLVED', 'DECLARATION_IMPORT_UNRESOLVED', 'RUN_NOT_SELECTED', 'DATA_NOT_CONFIRMED'])

const LIMITATION_CODES = new Set([
  'SECRET_FILE_EXCLUDED',
  'DYNAMIC_IMPORT',
  'DYNAMIC_IMPORT_UNRESOLVED',
  'SOURCE_PARSE_ERROR',
  'SYNTAX_ERROR',
  'PYTHON_VERSION_CONFLICT',
])

const SECTION: Record<string, AnalysisSection> = {
  SECRET_DETECTED: 'files',
  SECRET_FILE_EXCLUDED: 'files',
  NO_PROJECT_FILES: 'files',
  DEPENDENCY_UNRESOLVED: 'dependencies',
  DECLARATION_IMPORT_UNRESOLVED: 'dependencies',
  VERSION_UNKNOWN: 'dependencies',
  MODULE_EXCLUDED: 'dependencies',
  DYNAMIC_IMPORT: 'files',
  DYNAMIC_IMPORT_UNRESOLVED: 'files',
  SOURCE_PARSE_ERROR: 'files',
  RUN_NOT_SELECTED: 'run',
  RUN_TARGET_MISSING: 'run',
  PYTHON_VERSION_UNKNOWN: 'run',
  PYTHON_VERSION_CONFLICT: 'run',
  DATA_NOT_CONFIRMED: 'data',
  DATA_DEPENDENCIES_UNVERIFIED: 'data',
  DATA_FILES_SUMMARY: 'data',
}

export function getFindingKind(finding: Finding): FindingKind {
  if (finding.severity === 'error') return 'blocker'
  if (QUESTION_CODES.has(finding.code)) return 'question'
  if (LIMITATION_CODES.has(finding.code)) return 'limitation'
  return 'note'
}

export function getFindingSection(finding: Finding): AnalysisSection {
  return SECTION[finding.code] ?? 'notes'
}

const at = (f: Finding) => (f.path ? `${f.path}${f.line ? `:${f.line}` : ''}` : '')

const ALIASES: Record<string, string> = {
  DECLARATION_IMPORT_UNRESOLVED: 'DEPENDENCY_UNRESOLVED',
  DYNAMIC_IMPORT_UNRESOLVED: 'DYNAMIC_IMPORT',
  SYNTAX_ERROR: 'SOURCE_PARSE_ERROR',
}

export function describeFinding(f: Finding): string {
  const code = ALIASES[f.code] ?? f.code
  const key = `findings.${code}`
  const text = t(key, { path: f.path ?? '', module: f.module ?? '', at: at(f) })
  return text === key ? f.message : text
}
