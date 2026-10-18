import type { PackagePlan } from '../model/types'

export function isPackageBlocked(plan: PackagePlan | null | undefined): boolean {
  return plan?.state === 'blocked' || plan?.automation?.phase === 'blocked'
}
