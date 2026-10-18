import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import { sessionGeneration } from '@/shared/api'
import { projectApi } from '../api'
import type { Project } from './types'

type LoadStatus = 'idle' | 'loading' | 'ready' | 'error'

export const useProjectStore = defineStore('project', () => {
  const items = ref<Project[]>([])
  const status = ref<LoadStatus>('idle')
  let requestId = 0

  async function fetchAll(teamId: string) {
    const currentRequest = ++requestId
    status.value = 'loading'
    items.value = []
    try {
      const projects = await projectApi.list(teamId)
      if (currentRequest !== requestId) return
      items.value = projects
      status.value = 'ready'
    } catch {
      if (currentRequest === requestId) status.value = 'error'
    }
  }

  function reset() {
    requestId += 1
    items.value = []
    status.value = 'idle'
  }

  watch(sessionGeneration, reset, { flush: 'sync' })

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

  function remove(id: string) {
    items.value = items.value.filter((project) => project.id !== id)
  }

  return { items, status, reset, fetchAll, getById, upsert, patch, remove }
})
