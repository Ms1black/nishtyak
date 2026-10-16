import type {
  AnalysisDecisions,
  AnalysisOptions,
  AdviceFeature,
  DependencyLock,
  DecisionsDocument,
  FileContent,
  PackagePlan,
  Project,
  ProjectAnalysis,
  ProjectAdvice,
  TargetPlatform,
} from '../model/types'

export interface ProjectApi {
  list(teamId: string): Promise<Project[]>
  upload(teamId: string, file: File): Promise<Project>
  getAnalysis(projectId: string): Promise<ProjectAnalysis>
  getFileContent(projectId: string, path: string): Promise<FileContent>
  getDecisions(projectId: string): Promise<DecisionsDocument>
  saveDecisions(projectId: string, decisions: AnalysisDecisions, expectedRevision: number): Promise<DecisionsDocument>
  plan(projectId: string, decisions: AnalysisDecisions): Promise<PackagePlan>
  buildArchive(projectId: string, options?: { includeDockerfile?: boolean; lockId?: string }): Promise<Project>
  downloadArchive(projectId: string): Promise<Blob>
  reanalyze(projectId: string, options: AnalysisOptions, authorEnvironment?: unknown): Promise<Project>
  resolve(
    projectId: string,
    target: { pythonVersion: string; platform: TargetPlatform },
    decisions?: AnalysisDecisions,
  ): Promise<DependencyLock>
  getLock(projectId: string): Promise<DependencyLock>
  advice(projectId: string, feature: AdviceFeature, description: string): Promise<ProjectAdvice>
  getGraph(projectId: string): Promise<unknown>
  remove(projectId: string): Promise<void>
}
