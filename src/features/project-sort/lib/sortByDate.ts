export type DateSortOrder = 'newest' | 'oldest'

export function sortByDate<T extends { updatedAt: string }>(items: T[], order: DateSortOrder): T[] {
  const direction = order === 'newest' ? -1 : 1
  return [...items].sort((a, b) => direction * (Date.parse(a.updatedAt) - Date.parse(b.updatedAt)))
}
