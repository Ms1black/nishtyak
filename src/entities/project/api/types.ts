import type {
  AnalysisDecisions,
  AnalysisOptions,
  DependencyLock,
  DecisionsDocument,
  ClarificationSubmission,
  FileContent,
  PackagePlan,
  Project,
  ProjectAnalysis,
  ServiceCapabilities,
  TargetPlatform,
} from '../model/types'

export interface ProjectApi {
  capabilities(): Promise<ServiceCapabilities>
  list(teamId: string): Promise<Project[]>
  upload(teamId: string, file: File): Promise<Project>
  getAnalysis(projectId: string): Promise<ProjectAnalysis>
  getFileContent(projectId: string, path: string): Promise<FileContent>
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
  reanalyze(projectId: string, options: AnalysisOptions, authorEnvironment?: unknown): Promise<Project>
  resolve(
    projectId: string,
    target: { pythonVersion: string; platform: TargetPlatform },
    decisions?: AnalysisDecisions,
  ): Promise<DependencyLock>
  getLock(projectId: string): Promise<DependencyLock>
  getGraph(projectId: string): Promise<unknown>
  remove(projectId: string): Promise<void>
}
