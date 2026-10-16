import type {
  AnalysisDecisions,
  DecisionsDocument,
  FileContent,
  PackagePlan,
  Project,
  ProjectAnalysis,
} from '../model/types'

export interface ProjectApi {
  list(teamId: string): Promise<Project[]>
  upload(teamId: string, file: File): Promise<Project>
  getAnalysis(projectId: string): Promise<ProjectAnalysis>
  getFileContent(projectId: string, path: string): Promise<FileContent>
  getDecisions(projectId: string): Promise<DecisionsDocument>
  saveDecisions(projectId: string, decisions: AnalysisDecisions, expectedRevision: number): Promise<DecisionsDocument>
  plan(projectId: string, decisions: AnalysisDecisions): Promise<PackagePlan>
  buildArchive(projectId: string): Promise<Project>
  downloadArchive(projectId: string): Promise<Blob>
}
