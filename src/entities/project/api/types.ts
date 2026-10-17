import type {
  AnalysisDecisions,
  AnalysisOptions,
  DependencyLock,
  DecisionsDocument,
  ClarificationSubmission,
  FileContent,
  PackagePlan,
  Project,
  ProjectLookup,
  ProjectAnalysis,
  ServiceCapabilities,
  TargetPlatform,
  VerificationDocument,
  VerificationSubmission,
} from '../model/types'

export interface ProjectApi {
  capabilities(): Promise<ServiceCapabilities>
  list(teamId: string): Promise<Project[]>
  get(projectId: string): Promise<ProjectLookup>
  upload(teamId: string, file: File): Promise<Project>
  getAnalysis(projectId: string): Promise<ProjectAnalysis>
  getFileContent(projectId: string, path: string): Promise<FileContent>
  getArchiveFileContent(projectId: string, path: string, archiveTag: string): Promise<FileContent>
  getDecisions(projectId: string): Promise<DecisionsDocument>
  saveDecisions(
    projectId: string,
    decisions: AnalysisDecisions,
    expectedRevision: number,
  ): Promise<DecisionsDocument>
  submitClarifications(projectId: string, answers: ClarificationSubmission): Promise<DecisionsDocument>
  plan(projectId: string, decisions: AnalysisDecisions): Promise<PackagePlan>
  buildArchive(
    projectId: string,
    options?: { includeDockerfile?: boolean; lockId?: string },
  ): Promise<Project>
  downloadArchive(projectId: string): Promise<Blob>
  getReadme(projectId: string): Promise<{ content: string }>
  getVerification(projectId: string): Promise<VerificationDocument>
  saveVerification(projectId: string, verification: VerificationSubmission): Promise<VerificationDocument>
  replaceSource(projectId: string, file: File, expectedRevision: number): Promise<Project>
  reanalyze(projectId: string, options: AnalysisOptions, authorEnvironment?: unknown): Promise<Project>
  resolve(
    projectId: string,
    target: { pythonVersion: string; platform: TargetPlatform },
    decisions?: AnalysisDecisions,
  ): Promise<DependencyLock>
  getLock(projectId: string): Promise<DependencyLock>
  remove(projectId: string): Promise<void>
}
