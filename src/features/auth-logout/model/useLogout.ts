import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/entities/user'
import { RouteName } from '@/shared/config'

export function useLogout() {
  const userStore = useUserStore()
  const router = useRouter()
  const isLoggingOut = ref(false)

  async function logout() {
    isLoggingOut.value = true
    try {
      await userStore.logout()
    } finally {
      window.location.assign(router.resolve({ name: RouteName.Login }).href)
    }
  }

  return { logout, isLoggingOut }
}
