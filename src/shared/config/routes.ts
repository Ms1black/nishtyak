export const RouteName = {
  Upload: 'upload',
  TeamProjects: 'team-projects',
  TeamMembers: 'team-members',
  TeamInvite: 'team-invite',
  Profile: 'profile',
  Terms: 'terms',
  Login: 'login',
} as const

export type RouteName = (typeof RouteName)[keyof typeof RouteName]
