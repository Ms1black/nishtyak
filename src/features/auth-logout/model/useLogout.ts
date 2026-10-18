import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/entities/user'
import { RouteName } from '@/shared/config'
import { isLocalWorkspace, isPreviewWorkspace } from '@/shared/api'

export function useLogout() {
  const userStore = useUserStore()
  const router = useRouter()
  const isLoggingOut = ref(false)

  async function logout() {
    if (isLocalWorkspace.value) {
      router.push({ name: RouteName.Login })
      return
    }
    const destination = isPreviewWorkspace.value ? '/preview/access' : router.resolve({ name: RouteName.Login }).href
    isLoggingOut.value = true
    try {
      await userStore.logout()
    } finally {
      window.location.assign(destination)
    }
  }

  return { logout, isLoggingOut }
}
