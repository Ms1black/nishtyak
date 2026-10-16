import type { Finding } from '@/entities/project/model/types'

export type ReviewNotice = 'data' | 'environment' | 'launch' | 'other'

const GROUPS: Record<string, ReviewNotice> = {
  DATA_NOT_CONFIRMED: 'data',
  DATA_DEPENDENCIES_UNVERIFIED: 'data',
  INPUT_PATH_UNVERIFIED: 'data',
  MISSING_DATA: 'data',
  MISSING_INPUT_FILE: 'data',
  PYTHON_VERSION_UNKNOWN: 'environment',
  PYTHON_VERSION_CONFLICT: 'environment',
  PYTHON_PROFILE_MISMATCH: 'environment',
  VERSION_UNKNOWN: 'environment',
  VERSION_NOT_PINNED: 'environment',
  RUN_NOT_SELECTED: 'launch',
  RUN_TARGET_UNKNOWN: 'launch',
  RUN_TARGET_UNCERTAIN: 'launch',
  RUN_TARGET_AMBIGUOUS: 'launch',
}

const DEPENDENCY_QUESTIONS = new Set(['DEPENDENCY_UNRESOLVED', 'DECLARATION_IMPORT_UNRESOLVED'])
const REPORT_ONLY = new Set(['README_MISSING', 'LICENSE_MISSING'])
const NOTICE_ORDER: ReviewNotice[] = ['data', 'environment', 'launch', 'other']

export function reviewNotices(findings: Finding[]): ReviewNotice[] {
  const notices = new Set<ReviewNotice>()
  for (const finding of findings) {
    if (
      finding.severity !== 'warning' ||
      DEPENDENCY_QUESTIONS.has(finding.code) ||
      REPORT_ONLY.has(finding.code)
    )
      continue
    notices.add(GROUPS[finding.code] ?? 'other')
  }
  return NOTICE_ORDER.filter((kind) => notices.has(kind))
}
