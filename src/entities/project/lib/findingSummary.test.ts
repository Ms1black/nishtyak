import { describe, expect, it, vi } from 'vitest'
import type { Finding } from '../model/types'
import { summarizeFindings } from './findingSummary'

vi.mock('@/shared/i18n', () => ({ t: (key: string) => key }))
vi.mock('./findings', () => ({ describeFinding: (finding: Finding) => finding.message }))

function finding(code: string, extra: Partial<Finding> = {}): Finding {
  return { id: code, code, message: code, severity: 'warning', ...extra }
}

describe('human-readable analysis report', () => {
  it('collapses repeated data uncertainty and version warnings while retaining the concrete input', () => {
    const findings = [
      finding('DATA_DEPENDENCIES_UNVERIFIED'),
      finding('DATA_FILES_SUMMARY'),
      finding('INPUT_PATH_UNVERIFIED', { path: 'experiment.py', line: 6 }),
      finding('DATA_NOT_CONFIRMED'),
      finding('VERSION_UNKNOWN', { id: 'numpy', module: 'numpy' }),
      finding('VERSION_NOT_PINNED', { id: 'pandas' }),
      finding('PYTHON_VERSION_UNKNOWN'),
    ]
    expect(summarizeFindings(findings).map((item) => item.text)).toEqual([
      'INPUT_PATH_UNVERIFIED',
      'findings.VERSIONS_UNVERIFIED',
      'PYTHON_VERSION_UNKNOWN',
    ])
    expect(findings).toHaveLength(7)
  })

  it('retains distinct input locations and every actionable error', () => {
    expect(
      summarizeFindings([
        finding('INPUT_PATH_UNVERIFIED', { id: 'one', path: 'one.py', line: 1 }),
        finding('INPUT_PATH_UNVERIFIED', { id: 'two', path: 'two.py', line: 2 }),
        finding('SECRET_DETECTED', { id: 'secret-one', severity: 'error' }),
        finding('SECRET_DETECTED', { id: 'secret-two', severity: 'error' }),
      ]),
    ).toHaveLength(4)
  })

  it('shows one generic data limitation when no concrete missing input is known', () => {
    expect(
      summarizeFindings([finding('DATA_DEPENDENCIES_UNVERIFIED'), finding('DATA_NOT_CONFIRMED')]).map(
        (item) => item.text,
      ),
    ).toEqual(['findings.DATA_UNVERIFIED'])
  })

  it('does not ask to supply the README that has already been generated', () => {
    expect(
      summarizeFindings(
        [finding('README_MISSING'), finding('LICENSE_MISSING'), finding('RUN_INFERRED')],
        true,
      ).map((item) => item.text),
    ).toEqual(['LICENSE_MISSING', 'RUN_INFERRED'])
  })

  it('never hides unknown diagnostics or escalated errors with familiar codes', () => {
    const items = [
      finding('NEW_DIAGNOSTIC'),
      finding('README_MISSING', { severity: 'error' }),
      finding('VERSION_UNKNOWN', { severity: 'error', message: 'Version conflict' }),
    ]
    expect(summarizeFindings(items, true).map((item) => item.text)).toEqual([
      'NEW_DIAGNOSTIC',
      'README_MISSING',
      'Version conflict',
    ])
  })

  it('states unresolved dependencies as a limitation on the download screen', () => {
    expect(
      summarizeFindings(
        [
          finding('DEPENDENCY_UNRESOLVED', { module: 'optics' }),
          finding('DECLARATION_IMPORT_UNRESOLVED', { module: 'spectra' }),
        ],
        true,
      ).map((item) => item.text),
    ).toEqual(['findings.DEPENDENCIES_UNRESOLVED'])
  })
})
