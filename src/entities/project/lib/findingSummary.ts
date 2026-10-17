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
  locations: string[]
  modules: string[]
  sources: Finding[]
}

/** Condense presentation only; the original findings and evidence remain in the JSON report. */
export function summarizeFindings(findings: Finding[], residualOnly = false): FindingSummary[] {
  const result: FindingSummary[] = []
  const grouped = new Map<string, FindingSummary>()
  const hasDataDetails = findings.some((finding) => DATA_DETAILS.has(finding.code))

  for (const finding of findings) {
    const error = finding.severity === 'error'
    if (!error && finding.code === 'DATA_DEPENDENCIES_UNVERIFIED') continue
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
    // Group repeated explanations while retaining all their source locations and imports.
    const key = error
      ? `error:${finding.id}`
      : JSON.stringify([finding.severity, group ?? text])
    let item = grouped.get(key)
    if (!item) {
      item = { id: finding.id, severity: finding.severity, text, locations: [], modules: [], sources: [] }
      grouped.set(key, item)
      result.push(item)
    }
    const location = finding.path ? `${finding.path}${finding.line ? `:${finding.line}` : ''}` : null
    if (location && !item.locations.includes(location)) item.locations.push(location)
    const module = finding.module?.trim()
    if (module && !item.modules.includes(module)) item.modules.push(module)
    if (!item.sources.some((source) => source.path === finding.path && source.line === finding.line && source.module === finding.module)) {
      item.sources.push(finding)
    }
  }
  return result
}
