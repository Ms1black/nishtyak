import { USE_MOCK_API } from '@/shared/api'
import { projectMockApi } from './mock/projectApi.mock'
import { projectHttpApi } from './projectApi.http'
import type { ProjectApi } from './types'

export type { ProjectApi }

export const projectApi: ProjectApi = USE_MOCK_API ? projectMockApi : projectHttpApi
