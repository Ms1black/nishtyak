import type { Dependency } from '@/entities/project'
import { splitRequirement } from './requirement'

export function dependencyRequirement(
  dependency: Dependency,
  packageOverride?: string,
  versionOverride?: string,
): { name: string; spec: string } {
  const original = splitRequirement(dependency.requirement ?? dependency.package)
  const selected = packageOverride ? splitRequirement(packageOverride) : original
  let spec = selected.spec
  if (packageOverride && !spec.includes(';')) spec += marker(original.spec)
  if (versionOverride !== undefined) {
    const version = /^\d/.test(versionOverride) ? `==${versionOverride}` : versionOverride
    spec = version + marker(spec)
  }
  return {
    name: selected.name,
    spec,
  }
}

function marker(spec: string): string {
  const position = spec.indexOf(';')
  return position === -1 ? '' : `; ${spec.slice(position + 1).trim()}`
}

export function needsPackageMapping(
  dependency: Dependency,
  packageOverride?: string,
  excluded = false,
  unresolvedModules?: string[],
): boolean {
  if (excluded || dependency.category === 'local' || dependency.category === 'stdlib') return false
  if (unresolvedModules?.includes(dependency.module)) return true
  return !dependencyRequirement(dependency, packageOverride).name.trim()
}

export function dependencySummaries(
  dependencies: Dependency[],
  packages: Record<string, string>,
  versions: Record<string, string>,
  requirements?: string[],
  unresolvedModules?: string[],
): { id: string; name: string; spec: string }[] {
  if (requirements !== undefined) {
    return requirements.map((requirement, index) => ({
      id: `requirement-${index}`,
      ...splitRequirement(requirement),
    }))
  }
  const summaries = []
  for (const dependency of dependencies) {
    if (needsPackageMapping(dependency, packages[dependency.module], false, unresolvedModules)) continue
    const requirement = dependencyRequirement(
      dependency,
      packages[dependency.module],
      versions[dependency.module],
    )
    if (requirement.name) summaries.push({ id: dependency.id, ...requirement })
  }
  return summaries
}
