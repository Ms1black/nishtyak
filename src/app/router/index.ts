import { watch } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import { APP_NAME, RouteName } from '@/shared/config'
import { useUserStore } from '@/entities/user'
import { isStandaloneWorkspace, setUnauthorizedHandler } from '@/shared/api'
import { safeAuthRedirect } from '@/shared/lib/authRedirect'
import { locale, t } from '@/shared/i18n'
import { installChunkRecovery } from './chunkRecovery'

export const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', redirect: { name: RouteName.Upload } },
    {
      path: '/upload',
      name: RouteName.Upload,
      component: () => import('@/pages/upload'),
      meta: { title: 'routes.upload' },
    },
    { path: '/team', redirect: { name: RouteName.TeamProjects } },
    {
      path: '/team/projects',
      name: RouteName.TeamProjects,
      component: () => import('@/pages/team-projects'),
      meta: { title: 'routes.teamProjects' },
    },
    {
      path: '/team/members',
      name: RouteName.TeamMembers,
      component: () => import('@/pages/team-members'),
      meta: { title: 'routes.teamMembers' },
    },
    {
      path: '/team/invite',
      name: RouteName.TeamInvite,
      component: () => import('@/pages/team-invite'),
      meta: { title: 'routes.teamInvite' },
    },
    {
      path: '/profile',
      name: RouteName.Profile,
      component: () => import('@/pages/profile'),
      meta: { title: 'routes.profile' },
    },
    {
      path: '/terms',
      name: RouteName.Terms,
      component: () => import('@/pages/terms'),
      meta: { title: 'routes.terms', public: true },
    },
    {
      path: '/login',
      name: RouteName.Login,
      component: () => import('@/pages/login'),
      meta: { title: 'routes.login', public: true, guestOnly: true, layout: 'blank' },
    },
    { path: '/register', name: RouteName.Register, component: () => import('@/pages/login'), meta: { title: 'auth.registerTitle', public: true, guestOnly: true, layout: 'blank' } },
    { path: '/invite/team', name: RouteName.AcceptInvite, component: () => import('@/pages/accept-invite'), meta: { title: 'invite.join.title', public: true, layout: 'blank' } },
    { path: '/:pathMatch(.*)*', redirect: { name: RouteName.Upload } },
  ],
  scrollBehavior: (_to, _from, saved) => saved ?? { top: 0 },
})

installChunkRecovery(router, import.meta.url)

router.beforeEach(async (to) => {
  const userStore = useUserStore()
  await userStore.ensureSession()
  if (isStandaloneWorkspace.value) return true
  if (to.meta.guestOnly && userStore.isAuthenticated) return safeAuthRedirect(to.query.redirect)
  if (!to.meta.public && !userStore.isAuthenticated) {
    return { name: RouteName.Login, query: to.fullPath !== '/' ? { redirect: to.fullPath } : {} }
  }
})

setUnauthorizedHandler(() => {
  useUserStore().markAnonymous()
  const current = router.currentRoute.value
  if (!current.meta.public) router.replace({ name: RouteName.Login, query: { redirect: current.fullPath } })
})

function updateTitle() {
  const key = router.currentRoute.value.meta.title
  document.title = key ? `${t(key)} · ${APP_NAME}` : APP_NAME
}

router.afterEach(updateTitle)
watch(locale, updateTitle)
