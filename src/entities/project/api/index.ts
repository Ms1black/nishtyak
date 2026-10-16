import { projectHttpApi } from './projectApi.http'
import type { ProjectApi } from './types'

export type { ProjectApi }

export const projectApi: ProjectApi = projectHttpApi
