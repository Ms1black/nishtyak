export const ACCEPTED_EXTENSIONS = ['.zip'] as const
export const MAX_FILE_SIZE = 10 * 1024 * 1024

export function validateProjectFile(file: File): string | null {
  const name = file.name.toLowerCase()
  if (!ACCEPTED_EXTENSIONS.some((ext) => name.endsWith(ext))) {
    return 'upload.errors.type'
  }
  if (file.size === 0) return 'upload.errors.empty'
  if (file.size > MAX_FILE_SIZE) return 'upload.errors.size'
  return null
}
