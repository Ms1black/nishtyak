const base = `${import.meta.env.BASE_URL}mock/avatars/`

export const MOCK_USER_PHOTOS: Record<string, string> = {
  'u-1': `${base}user-alexey.jpg`,
  'u-2': `${base}user-maria.jpg`,
  'u-4': `${base}user-darya.jpg`,
}

export const MOCK_TEAM_PHOTOS: Record<string, string> = {
  't-nishtyak': `${base}team-nishtyak.jpg`,
}
