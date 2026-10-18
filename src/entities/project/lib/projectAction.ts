import type { Project } from '../model/types'

export type ProjectActionKind = 'refine' | 'details' | 'build' | 'download'

export interface ProjectAction {
  kind: ProjectActionKind
  label: string
}

export function getProjectAction(project: Project): ProjectAction {
  if (project.analysis === 'analyzing' || project.analysis === 'failed') {
    return { kind: 'details', label: 'project.action.details' }
  }
  if (project.analysis === 'partial') return { kind: 'refine', label: 'project.action.refine' }
  if (project.archive.status === 'ready') return { kind: 'download', label: 'project.action.download' }
  return { kind: 'build', label: 'project.action.build' }
}
