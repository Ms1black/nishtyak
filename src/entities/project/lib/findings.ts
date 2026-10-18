import { t } from '@/shared/i18n'
import type { Finding } from '../model/types'

export type AnalysisSection = 'files' | 'dependencies' | 'run' | 'data' | 'notes'

export type FindingKind = 'blocker' | 'question' | 'limitation' | 'note'

const QUESTION_CODES = new Set(['DEPENDENCY_UNRESOLVED', 'DECLARATION_IMPORT_UNRESOLVED'])

const LIMITATION_CODES = new Set([
  'SECRET_FILE_EXCLUDED',
  'DYNAMIC_IMPORT',
  'DYNAMIC_IMPORT_UNRESOLVED',
  'SOURCE_PARSE_ERROR',
  'SYNTAX_ERROR',
  'PYTHON_VERSION_CONFLICT',
  'PYTHON_VERSION_UNKNOWN',
  'VERSION_UNKNOWN',
  'VERSION_NOT_PINNED',
  'RUN_NOT_SELECTED',
  'RUN_TARGET_AMBIGUOUS',
  'RUN_TARGET_UNCERTAIN',
  'RUN_TARGET_UNKNOWN',
  'DATA_NOT_CONFIRMED',
  'DATA_DEPENDENCIES_UNVERIFIED',
  'INPUT_PATH_UNVERIFIED',
  'EXTERNAL_INPUT_REFERENCE',
  'MISSING_DATA',
  'MISSING_INPUT_FILE',
])

const SECTION: Record<string, AnalysisSection> = {
  SECRET_DETECTED: 'files',
  SECRET_FILE_EXCLUDED: 'files',
  NO_PROJECT_FILES: 'files',
  LOCAL_MODULE_EXCLUDED: 'files',
  LOCAL_MODULE_MISSING: 'files',
  DEPENDENCY_UNRESOLVED: 'dependencies',
  DECLARATION_IMPORT_UNRESOLVED: 'dependencies',
  VERSION_UNKNOWN: 'dependencies',
  VERSION_NOT_PINNED: 'dependencies',
  MODULE_EXCLUDED: 'dependencies',
  DYNAMIC_IMPORT: 'files',
  DYNAMIC_IMPORT_UNRESOLVED: 'files',
  SOURCE_PARSE_ERROR: 'files',
  SYNTAX_ERROR: 'files',
  RUN_NOT_SELECTED: 'run',
  RUN_TARGET_MISSING: 'run',
  RUN_WORKING_DIRECTORY_MISSING: 'run',
  RUN_TARGET_AMBIGUOUS: 'run',
  RUN_TARGET_UNCERTAIN: 'run',
  RUN_TARGET_UNKNOWN: 'run',
  RUN_INFERRED: 'run',
  RUN_AUTOMATIC: 'run',
  PYTHON_VERSION_UNKNOWN: 'run',
  PYTHON_VERSION_CONFLICT: 'run',
  DATA_NOT_CONFIRMED: 'data',
  DATA_DEPENDENCIES_UNVERIFIED: 'data',
  DATA_FILES_SUMMARY: 'data',
  INPUT_PATH_UNVERIFIED: 'data',
  EXTERNAL_INPUT_REFERENCE: 'data',
  MISSING_DATA: 'data',
  MISSING_INPUT_FILE: 'data',
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

const MODULE_MESSAGES = new Set([
  'DEPENDENCY_UNRESOLVED',
  'VERSION_UNKNOWN',
  'MODULE_EXCLUDED',
  'LOCAL_MODULE_EXCLUDED',
])

export function describeFinding(f: Finding): string {
  if (f.code === 'INPUT_PATH_UNVERIFIED' || f.code === 'EXTERNAL_INPUT_REFERENCE') return f.message
  const code = ALIASES[f.code] ?? f.code
  const module = f.module?.trim()
  if (MODULE_MESSAGES.has(code) && !module) return f.message
  const key = `findings.${code}`
  const text = t(key, { path: f.path ?? '', module: module ?? '', at: at(f) })
  return text === key ? f.message : text
}
