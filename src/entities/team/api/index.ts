import { USE_MOCK_API } from '@/shared/api'
import { teamHttpApi } from './teamApi.http'
import { teamMockApi } from './teamApi.mock'
import type { TeamApi } from './types'

export type { TeamApi }

export const teamApi: TeamApi = USE_MOCK_API ? teamMockApi : teamHttpApi
