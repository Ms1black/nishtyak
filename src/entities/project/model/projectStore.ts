import { defineStore } from 'pinia'
import { ref } from 'vue'
import { projectApi } from '../api'
import type { Project } from './types'

type LoadStatus = 'idle' | 'loading' | 'ready' | 'error'

export const useProjectStore = defineStore('project', () => {
  const items = ref<Project[]>([])
  const status = ref<LoadStatus>('idle')

  async function fetchAll(teamId: string) {
    status.value = 'loading'
    try {
      items.value = await projectApi.list(teamId)
      status.value = 'ready'
    } catch {
      status.value = 'error'
    }
  }

  function getById(id: string): Project | undefined {
    return items.value.find((p) => p.id === id)
  }

  function upsert(project: Project) {
    const index = items.value.findIndex((p) => p.id === project.id)
    if (index === -1) items.value.unshift(project)
    else items.value[index] = project
  }

  function patch(id: string, changes: Partial<Project>) {
    const project = getById(id)
    if (project) upsert({ ...project, ...changes })
  }

  return { items, status, fetchAll, getById, upsert, patch }
})
