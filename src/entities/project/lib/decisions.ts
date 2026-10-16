import type { AnalysisDecisions, ProjectAnalysis } from '../model/types'

export function createDefaultDecisions(analysis: ProjectAnalysis): AnalysisDecisions {
  const [onlyEntry] = analysis.entrypoints.length === 1 ? analysis.entrypoints : []
  return {
    analysisId: analysis.analysisId,
    packages: {},
    versions: {},
    excludedModules: [],
    excludedFiles: analysis.files.filter((f) => f.secret).map((f) => f.path),
    runSpec: onlyEntry ? { kind: 'script', target: onlyEntry, args: [], workingDirectory: '.' } : null,
    pythonVersion: null,
    description: '',
    inputDescription: '',
    expectedOutput: '',
    dataConfirmed: false,
  }
}

export function formatRunCommand(run: AnalysisDecisions['runSpec']): string {
  if (!run?.target) return 'python …'
  const args = run.args.length ? ` ${run.args.join(' ')}` : ''
  const cd = run.workingDirectory && run.workingDirectory !== '.' ? `cd ${run.workingDirectory} && ` : ''
  switch (run.kind) {
    case 'module':
      return `${cd}python -m ${run.target}${args}`
    case 'callable': {
      const [module, fn] = run.target.split(':')
      return `${cd}python -c "from ${module} import ${fn ?? 'main'}; ${fn ?? 'main'}()"${args}`
    }
    default:
      return `${cd}python ${run.target}${args}`
  }
}
