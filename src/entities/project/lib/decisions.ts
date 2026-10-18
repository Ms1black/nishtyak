import type { AnalysisDecisions, ProjectAnalysis } from '../model/types'

export function createDefaultDecisions(analysis: ProjectAnalysis): AnalysisDecisions {
  return {
    analysisId: analysis.analysisId,
    packages: {},
    versions: {},
    excludedModules: [],
    excludedFiles: analysis.files.filter((f) => f.secret).map((f) => f.path),
    runSpec: null,
    pythonVersion: null,
    description: '',
    inputDescription: '',
    expectedOutput: '',
    dataConfirmed: false,
    selectedExtras: [],
    declarationActions: {},
  }
}

export function formatRunCommand(run: AnalysisDecisions['runSpec']): string {
  if (!run?.target) return 'python …'
  const args = run.args.length ? ` ${run.args.map(quoteArgument).join(' ')}` : ''
  const cd = run.workingDirectory && run.workingDirectory !== '.' ? `cd ${quoteArgument(run.workingDirectory)} && ` : ''
  switch (run.kind) {
    case 'module':
      return `${cd}python -m ${quoteArgument(run.target)}${args}`
    case 'callable': {
      return 'python run_project.py'
    }
    default:
      return `${cd}python ${quoteArgument(run.target)}${args}`
  }
}

function quoteArgument(value: string): string {
  return /^[A-Za-z0-9_./:@=+-]+$/.test(value) ? value : `'${value.replaceAll("'", "'\"'\"'")}'`
}
