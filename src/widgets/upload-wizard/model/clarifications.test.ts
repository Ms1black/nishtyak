import { describe, expect, it } from 'vitest'
import { collectClarificationChanges, mergeDecisionChanges } from './clarifications'
import { createDefaultDecisions } from '@/entities/project/lib/decisions'
import type { AutomationQuestion, ProjectAnalysis } from '@/entities/project/model/types'

describe('clarification answer collection', () => {
  it('collects known answers without fabricating values for blanks or unbound text', () => {
    const questions: AutomationQuestion[] = [
      { id: 'package', aspect: 'dependencies', kind: 'package_mapping', module: 'sklearn' },
      { id: 'unknown', aspect: 'dependencies', kind: 'package_mapping', module: 'custom' },
      { id: 'requirement', aspect: 'environment', kind: 'version_constraint', declarationId: 'declared' },
      { id: 'version', aspect: 'environment', kind: 'version_constraint', module: 'pandas' },
      { id: 'text', aspect: 'description', kind: 'text' },
    ]
    expect(collectClarificationChanges(questions, {
      package: ' scikit-learn ', unknown: ' ', requirement: 'numpy>=1.26', version: '>=2', text: 'not a decision field',
    }, [])).toEqual({
      packages: { sklearn: 'scikit-learn' }, versions: { pandas: '>=2' },
      declarationActions: { declared: { action: 'replace', requirement: 'numpy>=1.26' } },
    })
  })

  it('accepts an entrypoint only when the candidate exists and is allowed by the question', () => {
    const question: AutomationQuestion = { id: 'launch', aspect: 'entrypoint', kind: 'entrypoint', candidateIds: ['known', 'missing'] }
    const candidate = { id: 'known', runSpec: { kind: 'script' as const, target: 'experiment.py', args: ['data.csv'], workingDirectory: '.' }, reason: 'main', confidence: 'high' }
    expect(collectClarificationChanges([question], { launch: 'missing' }, [candidate])).toEqual({})
    expect(collectClarificationChanges([{ ...question, candidateIds: [] }], { launch: 'known' }, [candidate])).toEqual({})
    const changes = collectClarificationChanges([question], { launch: 'known' }, [candidate])
    expect(changes.runSpec).toEqual(candidate.runSpec)
    expect(changes.runSpec?.args).not.toBe(candidate.runSpec.args)
  })

  it('preserves prior nested answers and copies run arguments when merging one batch', () => {
    const current = createDefaultDecisions({ analysisId: 'analysis', files: [] } as unknown as ProjectAnalysis)
    current.packages = { previous: 'requests' }
    current.versions = { previous: '>=2' }
    current.declarationActions = { old: { action: 'exclude' } }
    const changes = { packages: { custom: 'numpy' }, versions: { custom: '>=1' }, declarationActions: { next: { action: 'replace', requirement: 'numpy>=1' } } }
    const merged = mergeDecisionChanges(current, changes)
    expect(merged.packages).toEqual({ previous: 'requests', custom: 'numpy' })
    expect(merged.versions).toEqual({ previous: '>=2', custom: '>=1' })
    expect(Object.keys(merged.declarationActions)).toEqual(['old', 'next'])
    expect(current.packages).toEqual({ previous: 'requests' })
  })
})
