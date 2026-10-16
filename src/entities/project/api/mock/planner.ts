import type { AnalysisDecisions, Finding, PackagePlan, ProjectAnalysis } from '../../model/types'

const finding = (code: string, severity: Finding['severity'], extra: Partial<Finding> = {}): Finding => ({
  id: `${code}:${extra.path ?? extra.module ?? ''}`,
  code,
  severity,
  message: code,
  ...extra,
})

function minorOf(version: string): number {
  return Number(version.split('.')[1] ?? 0)
}

export function computePlan(analysis: ProjectAnalysis, decisions: AnalysisDecisions): PackagePlan {
  const findings: Finding[] = []
  let blocked = false
  let draft = analysis.status === 'partial'

  const excluded = new Set(decisions.excludedFiles)
  const included = analysis.files.filter((f) => !excluded.has(f.path))

  for (const f of analysis.findings) {
    findings.push(f)
    if (f.code === 'DYNAMIC_IMPORT' || f.code === 'SOURCE_PARSE_ERROR') draft = true
  }

  for (const file of analysis.files.filter((f) => f.secret)) {
    if (excluded.has(file.path)) {
      draft = true
      findings.push(finding('SECRET_FILE_EXCLUDED', 'warning', { path: file.path }))
    } else {
      blocked = true
      findings.push(finding('SECRET_DETECTED', 'error', { path: file.path }))
    }
  }

  const requirements: string[] = []
  for (const dep of analysis.dependencies) {
    if (dep.category === 'local' || dep.category === 'stdlib') continue
    if (decisions.excludedModules.includes(dep.module)) {
      findings.push(finding('MODULE_EXCLUDED', 'warning', { module: dep.module }))
      continue
    }
    const raw = decisions.packages[dep.module] || dep.requirement || dep.package
    if (!raw) {
      draft = true
      findings.push(finding('DEPENDENCY_UNRESOLVED', 'warning', { module: dep.module }))
      continue
    }
    const name = raw.split(/[<>=!~ ;[]/)[0]!.trim()
    const version = decisions.versions[dep.module] ?? decisions.versions[name]
    const requirement = version ? `${name}${version}` : raw
    if (!/[<>=~]/.test(requirement)) findings.push(finding('VERSION_UNKNOWN', 'info', { module: dep.module }))
    requirements.push(requirement)
  }

  const run = decisions.runSpec
  if (!run?.target) {
    draft = true
    findings.push(finding('RUN_NOT_SELECTED', 'warning'))
  } else if (run.kind === 'script') {
    const path = run.workingDirectory && run.workingDirectory !== '.' ? `${run.workingDirectory}/${run.target}` : run.target
    if (!included.some((f) => f.path === path)) {
      blocked = true
      findings.push(finding('RUN_TARGET_MISSING', 'error', { path }))
    }
  }

  if (!decisions.pythonVersion) {
    findings.push(finding('PYTHON_VERSION_UNKNOWN', 'info'))
  } else if (analysis.pythonRequires?.startsWith('>=')) {
    if (minorOf(decisions.pythonVersion) < minorOf(analysis.pythonRequires.slice(2))) {
      draft = true
      findings.push(finding('PYTHON_VERSION_CONFLICT', 'warning'))
    }
  }

  if (!decisions.dataConfirmed) {
    draft = true
    findings.push(finding('DATA_NOT_CONFIRMED', 'warning'))
  }

  if (!included.length) {
    blocked = true
    findings.push(finding('NO_PROJECT_FILES', 'error'))
  }

  return { state: blocked ? 'blocked' : draft ? 'draft' : 'ready_to_verify', requirements, findings }
}
