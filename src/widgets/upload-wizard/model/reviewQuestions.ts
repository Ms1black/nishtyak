import type { AnalysisDecisions, Finding } from '@/entities/project/model/types'

export interface ReviewQuestion {
  kind: 'data' | 'python' | 'launch'
  section: 'data' | 'run'
}

const INPUT_CODES = new Set([
  'INPUT_PATH_UNVERIFIED',
  'EXTERNAL_INPUT_REFERENCE',
  'MISSING_DATA',
  'MISSING_INPUT_FILE',
])

const LAUNCH_CODES = new Set([
  'RUN_NOT_SELECTED',
  'RUN_TARGET_UNKNOWN',
  'RUN_TARGET_UNCERTAIN',
  'RUN_TARGET_AMBIGUOUS',
])

export function reviewQuestions(findings: Finding[], decisions: AnalysisDecisions): ReviewQuestion[] {
  const codes = new Set(
    findings.filter((finding) => finding.severity !== 'error').map((finding) => finding.code),
  )
  const questions: ReviewQuestion[] = []
  if (
    !decisions.dataConfirmed &&
    !decisions.inputDescription.trim() &&
    [...codes].some((code) => INPUT_CODES.has(code))
  ) {
    questions.push({ kind: 'data', section: 'data' })
  }
  if (!decisions.pythonVersion && codes.has('PYTHON_VERSION_UNKNOWN')) {
    questions.push({ kind: 'python', section: 'run' })
  }
  if (!decisions.runSpec && [...codes].some((code) => LAUNCH_CODES.has(code))) {
    questions.push({ kind: 'launch', section: 'run' })
  }
  return questions
}
