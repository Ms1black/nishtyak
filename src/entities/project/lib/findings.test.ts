import { describe, expect, it, vi } from 'vitest'
import type { Finding } from '../model/types'
import { describeFinding, getFindingKind, getFindingSection } from './findings'

vi.mock('@/shared/i18n', () => ({
  t: (key: string, params: Record<string, string>) => {
    const messages: Record<string, string> = {
      'findings.DEPENDENCY_UNRESOLVED': 'Specify a package for import {module}',
      'findings.VERSION_UNKNOWN': 'No version for {module}',
      'findings.MODULE_EXCLUDED': 'Import {module} is excluded',
      'findings.DYNAMIC_IMPORT': 'Dynamic import in {at}',
    }
    return (messages[key] ?? key).replace(/\{(\w+)\}/g, (_match, name: string) => params[name] ?? '')
  },
}))

function finding(code: string, extra: Partial<Finding> = {}): Finding {
  return { id: code, code, severity: 'warning', message: `Details for ${code}`, ...extra }
}

describe('analysis finding guidance', () => {
  it.each(['DEPENDENCY_UNRESOLVED', 'DECLARATION_IMPORT_UNRESOLVED'])(
    'asks for clarification of an unresolved package mapping: %s',
    (code) => {
      expect(getFindingKind(finding(code))).toBe('question')
      expect(getFindingSection(finding(code))).toBe('dependencies')
    },
  )

  it.each([
    'DATA_NOT_CONFIRMED',
    'INPUT_PATH_UNVERIFIED',
    'EXTERNAL_INPUT_REFERENCE',
    'RUN_NOT_SELECTED',
    'RUN_TARGET_AMBIGUOUS',
    'RUN_TARGET_UNCERTAIN',
    'RUN_TARGET_UNKNOWN',
    'VERSION_UNKNOWN',
    'VERSION_NOT_PINNED',
    'PYTHON_VERSION_UNKNOWN',
  ])('keeps an uncertainty that permits a draft out of required questions: %s', (code) => {
    expect(getFindingKind(finding(code))).toBe('limitation')
  })

  it('keeps an inferred launch informational', () => {
    expect(getFindingKind(finding('RUN_INFERRED', { severity: 'info' }))).toBe('note')
    expect(getFindingSection(finding('RUN_INFERRED'))).toBe('run')
  })

  it.each(['LOCAL_MODULE_EXCLUDED', 'RUN_WORKING_DIRECTORY_MISSING', 'UNRECOGNIZED_ERROR'])(
    'blocks only when the service actually reports an error: %s',
    (code) => {
      expect(getFindingKind(finding(code, { severity: 'error' }))).toBe('blocker')
      expect(getFindingKind(finding(code))).not.toBe('blocker')
    },
  )

  it.each([
    ['LOCAL_MODULE_EXCLUDED', 'files'],
    ['SYNTAX_ERROR', 'files'],
    ['RUN_WORKING_DIRECTORY_MISSING', 'run'],
    ['INPUT_PATH_UNVERIFIED', 'data'],
    ['EXTERNAL_INPUT_REFERENCE', 'data'],
    ['MISSING_DATA', 'data'],
    ['MISSING_INPUT_FILE', 'data'],
    ['VERSION_NOT_PINNED', 'dependencies'],
  ])('sends %s to the section where it can be understood or fixed', (code, section) => {
    expect(getFindingSection(finding(code!))).toBe(section)
  })

  it.each([
    'DEPENDENCY_UNRESOLVED',
    'DECLARATION_IMPORT_UNRESOLVED',
    'VERSION_UNKNOWN',
    'MODULE_EXCLUDED',
    'LOCAL_MODULE_EXCLUDED',
  ])('preserves diagnostic detail instead of showing an empty module name: %s', (code) => {
    const diagnostic = finding(code, {
      message: 'Package for the import in experiment.py could not be identified.',
    })
    expect(describeFinding(diagnostic)).toBe(diagnostic.message)
    expect(describeFinding({ ...diagnostic, module: '  ' })).toBe(diagnostic.message)
  })

  it('uses the module name when evidence is available, including aliased codes', () => {
    expect(describeFinding(finding('DECLARATION_IMPORT_UNRESOLVED', { module: ' sklearn ' }))).toBe(
      'Specify a package for import sklearn',
    )
  })

  it('preserves unrecognized core diagnostics and source locations', () => {
    const diagnostic = finding('NEW_CORE_DIAGNOSTIC', { message: 'An important new detail.' })
    expect(describeFinding(diagnostic)).toBe(diagnostic.message)
    expect(describeFinding(finding('DYNAMIC_IMPORT_UNRESOLVED', { path: 'experiment.py', line: 12 }))).toBe(
      'Dynamic import in experiment.py:12',
    )
  })
})
