import { teamHttpApi } from './teamApi.http'
import type { TeamApi } from './types'

export type { TeamApi }
export const teamApi: TeamApi = teamHttpApi
