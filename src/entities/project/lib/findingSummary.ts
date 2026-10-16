import { t } from '@/shared/i18n'
import type { Finding } from '../model/types'
import { describeFinding } from './findings'

const DATA_DEFAULTS = new Set(['DATA_DEPENDENCIES_UNVERIFIED', 'DATA_NOT_CONFIRMED'])
const DATA_DETAILS = new Set([
  'INPUT_PATH_UNVERIFIED',
  'EXTERNAL_INPUT_REFERENCE',
  'MISSING_DATA',
  'MISSING_INPUT_FILE',
])
const VERSION_CODES = new Set(['VERSION_UNKNOWN', 'VERSION_NOT_PINNED'])
const UNRESOLVED_CODES = new Set(['DEPENDENCY_UNRESOLVED', 'DECLARATION_IMPORT_UNRESOLVED'])
const COMPLETED_INFORMATION = new Set(['README_MISSING', 'DATA_FILES_SUMMARY'])

export interface FindingSummary {
  id: string
  severity: Finding['severity']
  text: string
}

/** Condense presentation only; the original findings and evidence remain in the JSON report. */
export function summarizeFindings(findings: Finding[], residualOnly = false): FindingSummary[] {
  const result: FindingSummary[] = []
  const seen = new Set<string>()
  const hasDataDetails = findings.some((finding) => DATA_DETAILS.has(finding.code))

  for (const finding of findings) {
    const error = finding.severity === 'error'
    if (!error && residualOnly && COMPLETED_INFORMATION.has(finding.code)) continue
    if (!error && hasDataDetails && DATA_DEFAULTS.has(finding.code)) continue
    if (!error && finding.code === 'DATA_FILES_SUMMARY') continue

    let text = describeFinding(finding)
    let group: string | undefined
    if (!error && residualOnly && UNRESOLVED_CODES.has(finding.code)) {
      group = 'unresolved'
      text = t('findings.DEPENDENCIES_UNRESOLVED')
    } else if (!error && VERSION_CODES.has(finding.code)) {
      group = 'versions'
      text = t('findings.VERSIONS_UNVERIFIED')
    } else if (!error && DATA_DEFAULTS.has(finding.code)) {
      group = 'data'
      text = t('findings.DATA_UNVERIFIED')
    }
    // Keep distinct source locations and every error, even when translations coincide.
    const key = error
      ? `error:${finding.id}`
      : (group ?? JSON.stringify([finding.severity, text, finding.path, finding.line, finding.module]))
    if (seen.has(key)) continue
    seen.add(key)
    result.push({ id: finding.id, severity: finding.severity, text })
  }
  return result
}
