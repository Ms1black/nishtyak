import { t } from '@/shared/i18n'
import { formatBytes } from '@/shared/lib'
import type { Project } from '../model/types'

export function getProjectMeta(project: Pick<Project, 'sourceType' | 'sizeBytes' | 'filesCount'>): string[] {
  return [
    project.sourceType === 'zip' ? 'ZIP' : '.py',
    formatBytes(project.sizeBytes),
    t('units.files', project.filesCount),
  ]
}

export function getArchiveFileName(project: Pick<Project, 'name'>): string {
  return `${project.name}-ready.zip`
}

export function getSecretLabel(code: string | undefined): string {
  if (!code) return ''
  const key = `project.secret.${code}`
  const label = t(key)
  return label === key ? code : label
}
