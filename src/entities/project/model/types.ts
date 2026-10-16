export type ProjectSourceType = 'zip' | 'py'

export type AnalysisStatus = 'analyzing' | 'partial' | 'full' | 'failed'

export type ArchiveStatus = 'unavailable' | 'not_built' | 'building' | 'ready'

export interface ProjectArchive {
  status: ArchiveStatus
  sizeBytes?: number
}

export interface Project {
  id: string
  name: string
  sourceType: ProjectSourceType
  sizeBytes: number
  filesCount: number
  analysis: AnalysisStatus
  archive: ProjectArchive
  updatedAt: string
}

export interface ProjectFile {
  path: string
  size: number
  secret?: string
}

export interface ImportLocation {
  path: string
  line: number
}

export type DependencyCategory = 'external' | 'unresolved' | 'local' | 'stdlib'

export type DependencySource = 'declared' | 'inferred' | 'unresolved' | 'author_environment'

export interface Dependency {
  id: string
  module: string
  category: DependencyCategory
  package: string | null
  requirement: string | null
  source: DependencySource
  declaredIn?: string
  locations: ImportLocation[]
}

export type FindingSeverity = 'error' | 'warning' | 'info'

export interface Finding {
  id: string
  code: string
  severity: FindingSeverity
  message: string
  path?: string | null
  line?: number | null
  module?: string
}

export interface ProjectAnalysis {
  analysisId: string
  projectId: string
  status: AnalysisStatus
  error?: string
  files: ProjectFile[]
  dependencies: Dependency[]
  entrypoints: string[]
  pythonRequires: string | null
  findings: Finding[]
}

export type FileContent =
  | { kind: 'text'; content: string }
  | { kind: 'hidden' }
  | { kind: 'binary' }

export type RunKind = 'script' | 'module' | 'callable'

export interface RunSpec {
  kind: RunKind
  target: string
  args: string[]
  workingDirectory: string
}

export interface AnalysisDecisions {
  analysisId: string
  packages: Record<string, string>
  versions: Record<string, string>
  excludedModules: string[]
  excludedFiles: string[]
  runSpec: RunSpec | null
  pythonVersion: string | null
  description: string
  inputDescription: string
  expectedOutput: string
  dataConfirmed: boolean
}

export interface DecisionsDocument {
  revision: number
  decisions: AnalysisDecisions | null
  updatedAt: string | null
}

export type PlanState = 'blocked' | 'draft' | 'ready_to_verify'

export interface PackagePlan {
  state: PlanState
  requirements: string[]
  findings: Finding[]
}
