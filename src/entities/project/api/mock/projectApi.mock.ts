import { ApiError } from '@/shared/api'
import { delay } from '@/shared/lib'
import { createDefaultDecisions, formatRunCommand } from '../../lib/decisions'
import type { DecisionsDocument, FileContent, Project, ProjectAnalysis } from '../../model/types'
import type { ProjectApi } from '../types'
import { createZip } from './createZip'
import { computePlan } from './planner'
import { createAnalysis, createSeedProjects, FILE_CONTENTS } from './seed'

const ANALYSIS_DURATION_MS = 8000

const projects = new Map(createSeedProjects().map((p) => [p.id, p]))
const analyses = new Map<string, ProjectAnalysis>()
const decisionDocs = new Map<string, DecisionsDocument>()
const analyzingSince = new Map<string, number>()

function getProject(id: string): Project {
  const project = projects.get(id)
  if (!project) throw new ApiError(404, 'RESOURCE_NOT_FOUND')
  return project
}

function save(project: Project): Project {
  projects.set(project.id, project)
  return structuredClone(project)
}

function tickAnalysis(project: Project): Project {
  if (project.analysis !== 'analyzing') return project
  const since = analyzingSince.get(project.id) ?? Date.now()
  analyzingSince.set(project.id, since)
  if (Date.now() - since < ANALYSIS_DURATION_MS) return project
  analyses.delete(project.id)
  const done: Project = { ...project, analysis: 'full', archive: { status: 'not_built' }, updatedAt: new Date().toISOString() }
  projects.set(done.id, done)
  return done
}

function getAnalysisFor(project: Project): ProjectAnalysis {
  let analysis = analyses.get(project.id)
  if (!analysis) {
    analysis = createAnalysis(project)
    if (analysis.status !== 'analyzing') analyses.set(project.id, analysis)
  }
  return analysis
}

function getSavedDecisions(project: Project) {
  return decisionDocs.get(project.id)?.decisions ?? createDefaultDecisions(getAnalysisFor(project))
}

export const projectMockApi: ProjectApi = {
  async list() {
    await delay(400)
    return [...projects.values()].map((p) => structuredClone(tickAnalysis(p)))
  },

  async upload(_teamId, file) {
    await delay(1400)
    const isZip = file.name.toLowerCase().endsWith('.zip')
    const project: Project = {
      id: `p-${crypto.randomUUID()}`,
      name: file.name.replace(/\.(zip|py)$/i, ''),
      sourceType: isZip ? 'zip' : 'py',
      sizeBytes: file.size,
      filesCount: isZip ? 8 : 1,
      analysis: isZip ? 'partial' : 'full',
      archive: { status: 'not_built' },
      updatedAt: new Date().toISOString(),
    }
    analyses.set(project.id, createAnalysis(project, isZip ? undefined : file.name))
    return save(project)
  },

  async getAnalysis(projectId) {
    await delay(300)
    return structuredClone(getAnalysisFor(tickAnalysis(getProject(projectId))))
  },

  async getFileContent(projectId, path): Promise<FileContent> {
    await delay(150)
    const file = getAnalysisFor(getProject(projectId)).files.find((f) => f.path === path)
    if (!file) throw new ApiError(404, 'RESOURCE_NOT_FOUND')
    if (file.secret) return { kind: 'hidden' }
    if (/\.(sqlite|bin|pkl|png|jpg|parquet)$/i.test(path)) return { kind: 'binary' }
    return { kind: 'text', content: FILE_CONTENTS[path] ?? `# ${path}\n\ndef main():\n    ...\n\nif __name__ == "__main__":\n    main()` }
  },

  async getDecisions(projectId) {
    await delay(150)
    getProject(projectId)
    return structuredClone(decisionDocs.get(projectId) ?? { revision: 0, decisions: null, updatedAt: null })
  },

  async saveDecisions(projectId, decisions, expectedRevision) {
    await delay(250)
    getProject(projectId)
    const current = decisionDocs.get(projectId)?.revision ?? 0
    if (current !== expectedRevision) throw new ApiError(409, 'DECISIONS_REVISION_CONFLICT')
    const doc: DecisionsDocument = { revision: current + 1, decisions: structuredClone(decisions), updatedAt: new Date().toISOString() }
    decisionDocs.set(projectId, doc)
    return structuredClone(doc)
  },

  async plan(projectId, decisions) {
    await delay(200)
    const analysis = getAnalysisFor(getProject(projectId))
    if (decisions.analysisId !== analysis.analysisId) throw new ApiError(409, 'STALE_ANALYSIS')
    return computePlan(analysis, decisions)
  },

  async buildArchive(projectId) {
    await delay(1200)
    const project = getProject(projectId)
    const plan = computePlan(getAnalysisFor(project), getSavedDecisions(project))
    if (plan.state === 'blocked') throw new ApiError(409, 'PLAN_BLOCKED')
    const sizeBytes = Math.max(4 * 1024, Math.round(project.sizeBytes * 0.3))
    return save({ ...project, archive: { status: 'ready', sizeBytes }, updatedAt: new Date().toISOString() })
  },

  async downloadArchive(projectId) {
    await delay(300)
    const project = getProject(projectId)
    const analysis = getAnalysisFor(project)
    const decisions = getSavedDecisions(project)
    const plan = computePlan(analysis, decisions)
    const excluded = new Set(decisions.excludedFiles)
    const readme = [
      `# ${project.name}`,
      decisions.description,
      '## Запуск',
      '```bash\npip install -r requirements.txt\n' + formatRunCommand(decisions.runSpec) + '\n```',
      decisions.inputDescription && `## Входные данные\n${decisions.inputDescription}`,
      decisions.expectedOutput && `## Ожидаемый результат\n${decisions.expectedOutput}`,
    ]
      .filter(Boolean)
      .join('\n\n')
    return createZip([
      { name: 'README.md', content: `${readme}\n` },
      { name: 'requirements.txt', content: `${plan.requirements.join('\n')}\n` },
      ...analysis.files
        .filter((f) => !excluded.has(f.path))
        .map((f) => ({ name: f.path, content: FILE_CONTENTS[f.path] ?? '' })),
    ])
  },
}
