export function splitRequirement(requirement: string | null): { name: string; spec: string } {
  if (!requirement) return { name: '', spec: '' }
  const match = requirement.match(/^\s*([A-Za-z0-9._-]+(?:\[[^\]]*\])?)\s*(.*)$/)
  return match ? { name: match[1]!, spec: match[2]!.trim() } : { name: requirement, spec: '' }
}
