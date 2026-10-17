import type { AnalysisDecisions, AutomationQuestion, LaunchCandidate } from '@/entities/project'

export interface ClarificationAnswers {
  changes: Partial<AnalysisDecisions>
  notes: string
}

export function collectClarificationChanges(
  questions: AutomationQuestion[],
  values: Record<string, string>,
  candidates: LaunchCandidate[],
): Partial<AnalysisDecisions> {
  const changes: Partial<AnalysisDecisions> = {}
  for (const question of questions) {
    const value = values[question.id]?.trim()
    if (!value) continue
    if (question.kind === 'package_mapping' && question.module) {
      changes.packages ??= {}
      changes.packages[question.module] = value
    } else if (question.kind === 'version_constraint' && question.declarationId) {
      changes.declarationActions ??= {}
      changes.declarationActions[question.declarationId] = { action: 'replace', requirement: value }
    } else if (question.kind === 'version_constraint' && question.module) {
      changes.versions ??= {}
      changes.versions[question.module] = value
    } else if (question.kind === 'entrypoint' && question.candidateIds?.includes(value)) {
      const candidate = candidates.find((item) => item.id === value)
      if (candidate) changes.runSpec = { ...candidate.runSpec, args: [...candidate.runSpec.args] }
    }
  }
  return changes
}

export function mergeDecisionChanges(
  current: AnalysisDecisions,
  changes: Partial<AnalysisDecisions>,
): AnalysisDecisions {
  const merged = { ...current, ...changes }
  merged.packages = { ...current.packages, ...changes.packages }
  merged.versions = { ...current.versions, ...changes.versions }
  merged.declarationActions = { ...current.declarationActions, ...changes.declarationActions }
  if (merged.runSpec) merged.runSpec = { ...merged.runSpec, args: [...merged.runSpec.args] }
  return merged
}
