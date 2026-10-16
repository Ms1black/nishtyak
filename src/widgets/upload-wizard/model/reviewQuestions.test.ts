import { describe, expect, it } from 'vitest'
import type { AnalysisDecisions, Finding } from '@/entities/project/model/types'
import { reviewQuestions } from './reviewQuestions'

const decisions: AnalysisDecisions = {
  analysisId: 'example',
  packages: {},
  versions: {},
  excludedModules: [],
  excludedFiles: [],
  runSpec: null,
  pythonVersion: null,
  description: '',
  inputDescription: '',
  expectedOutput: '',
  dataConfirmed: false,
  selectedExtras: [],
  declarationActions: {},
}

function finding(code: string): Finding {
  return { id: code, code, severity: 'warning', message: code }
}

describe('optional review questions', () => {
  it('does not turn generic warnings or generated documents into another questionnaire', () => {
    const findings = [
      'README_MISSING',
      'LICENSE_MISSING',
      'DATA_NOT_CONFIRMED',
      'DATA_DEPENDENCIES_UNVERIFIED',
      'VERSION_NOT_PINNED',
      'RUN_INFERRED',
    ].map(finding)
    expect(reviewQuestions(findings, decisions)).toEqual([])
  })

  it('offers each useful topic once before export', () => {
    const findings = [
      'INPUT_PATH_UNVERIFIED',
      'MISSING_INPUT_FILE',
      'PYTHON_VERSION_UNKNOWN',
      'RUN_TARGET_AMBIGUOUS',
      'RUN_NOT_SELECTED',
    ].map(finding)
    expect(reviewQuestions(findings, decisions)).toEqual([
      { kind: 'data', section: 'data' },
      { kind: 'python', section: 'run' },
      { kind: 'launch', section: 'run' },
    ])
  })

  it('does not repeat questions the author has already answered', () => {
    const findings = ['INPUT_PATH_UNVERIFIED', 'PYTHON_VERSION_UNKNOWN', 'RUN_NOT_SELECTED'].map(finding)
    expect(
      reviewQuestions(findings, {
        ...decisions,
        inputDescription: 'Request measurements.csv from the experiment author.',
        pythonVersion: '3.11',
        runSpec: { kind: 'script', target: 'experiment.py', args: [], workingDirectory: '.' },
      }),
    ).toEqual([])
  })
})
