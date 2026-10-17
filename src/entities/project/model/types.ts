export type ProjectSourceType = 'zip' | 'py'

export type AnalysisStatus = 'analyzing' | 'partial' | 'full' | 'failed'

export type ArchiveStatus = 'unavailable' | 'not_built' | 'building' | 'ready'

export interface ProjectArchive {
  status: ArchiveStatus
  sizeBytes?: number
  files?: string[]
  sha256?: string
}

export interface ProjectLookup {
  project: Project
  teamId: string
}

export type VerificationStatus = 'not_checked' | 'passed' | 'failed' | 'unable'

export interface ManualVerification {
  status: VerificationStatus
  pythonVersion: string
  platform: string
  command: string
  output: string
  notes: string
  checkedAt: string
  checkedBy: { id: string; name: string }
}

export interface VerificationDocument {
  revision: number
  analysisId: string | null
  archiveTag: string | null
  stale: boolean
  verification: ManualVerification | null
}

export interface VerificationSubmission {
  expectedRevision: number
  analysisId: string
  archiveTag: string
  status: VerificationStatus
  pythonVersion?: string
  platform?: string
  command?: string
  output?: string
  notes?: string
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
  suggestedRunSpec?: RunSpec | null
  options?: AnalysisOptions
  projectRoots?: string[]
  optionalGroups?: string[]
  launchCandidates?: LaunchCandidate[]
  automaticReview?: AutomaticReview | null
  automaticPackages?: Record<string, string>
  automation?: ProjectAutomation | null
}

export type AutomationAspect = 'dependencies' | 'entrypoint' | 'description' | 'environment'

export interface AutomationQuestion {
  id: string
  aspect: AutomationAspect
  kind: 'package_mapping' | 'entrypoint' | 'version_constraint' | 'text'
  module?: string
  message?: string
  candidateIds?: string[]
  declarationId?: string
  code?: string
  filePath?: string | null
}

export interface ProjectAutomation {
  phase: 'static' | 'assisting' | 'validating' | 'finalizing' | 'needs_input' | 'ready' | 'draft' | 'blocked'
  stage?: 'pre_human' | 'post_human' | 'complete'
  humanRound?: 0 | 1
  finalAttempt?: 0 | 1
  questionnaireId?: string | null
  questions?: AutomationQuestion[]
  aspect?: AutomationAspect
  attempt?: number
  maxAttempts?: number
  reason?: string | null
  question?: AutomationQuestion | null
}

export interface AutomaticReview {
  status: 'disabled' | 'pending' | 'running' | 'ready' | 'unavailable' | 'skipped'
  provider: 'deepseek'
  applied: boolean
  reason?: string | null
  elapsedMs?: number
  entrypoint?: { applied: boolean; candidateId?: string | null; reason: string; evidenceIds: string[]; explanation?: string }
}

export interface ServiceCapabilities {
  automaticReview: { enabled: boolean; provider: 'deepseek'; budgetSeconds: number; features?: string[] }
  automaticResolution?: { enabled: boolean; budgetSeconds: number; defaultPlatform: TargetPlatform }
  automation?: { enabled: boolean; maxAttempts: number }
}

export interface AnalysisOptions {
  pythonVersion: string
  projectRoot: string | null
  sourceRoots: string[]
}

export interface LaunchCandidate {
  id: string
  runSpec: RunSpec
  reason: string
  confidence: string
}

export type FileContent =
  | { kind: 'text'; content: string; truncated?: boolean }
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
  selectedExtras: string[]
  declarationActions: Record<string, { action: string; requirement?: string | null }>
}

export interface DecisionsDocument {
  revision: number
  decisions: AnalysisDecisions | null
  updatedAt: string | null
}

export interface ClarificationSubmission {
  questionnaireId: string
  expectedRevision: number
  decisions: AnalysisDecisions
  notes?: string
}

export type PlanState = 'blocked' | 'draft' | 'ready_to_verify'

export interface PackagePlan {
  state: PlanState
  requirements: string[]
  findings: Finding[]
  runSpec?: RunSpec | null
  runSpecOrigin?: 'author' | 'automatic' | 'inferred' | 'unknown'
  pythonVersion?: string | null
  pythonVersionOrigin?: 'author' | 'author_environment' | 'inferred' | 'unknown'
  files?: string[]
  decisions?: AnalysisDecisions
  description?: string
  descriptionOrigin?: 'author' | 'automatic' | 'inferred' | 'unknown'
  automaticReview?: AutomaticReview | null
  automaticResolution?: AutomaticResolution | null
  automaticPackages?: Record<string, string>
  automation?: ProjectAutomation | null
}

export interface AutomaticResolution {
  status: 'pending' | 'running' | 'ready' | 'needs_input' | 'unavailable' | 'disabled'
  reason?: string
  target?: { pythonVersion: string; platform: TargetPlatform }
  lockId?: string
  pythonSource?: 'author_environment' | 'author' | 'declared' | 'analysis_default'
  targetSource?: 'author' | 'automatic'
  platformSource?: 'author_environment' | 'service_default' | 'author'
  packages?: string[]
  provenance?: string
  elapsedMs?: number
}

export type TargetPlatform =
  | 'linux-x86_64'
  | 'linux-aarch64'
  | 'macos-arm64'
  | 'macos-x86_64'
  | 'windows-x86_64'

export interface DependencyLock {
  id: string
  analysis_id: string
  target: { python_version: string; platform: TargetPlatform }
  packages: string[]
  requirements: string
  provenance: string
}
