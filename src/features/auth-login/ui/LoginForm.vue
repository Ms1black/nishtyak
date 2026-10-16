<script setup lang="ts">
import { computed, ref } from 'vue'
import { useUserStore } from '@/entities/user'
import { ApiError, isStandaloneWorkspace, USE_MOCK_API } from '@/shared/api'
import { BaseButton, BaseIcon, BaseInput } from '@/shared/ui'

const emit = defineEmits<{ success: [] }>()

const userStore = useUserStore()
const email = ref('')
const password = ref('')
const showPassword = ref(false)
const isSubmitting = ref(false)
const touched = ref(false)
const serverError = ref<string | null>(null)

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const emailError = computed(() => (touched.value && !EMAIL_RE.test(email.value) ? 'auth.errors.email' : null))
const passwordError = computed(() => (touched.value && password.value.length < 6 ? 'auth.errors.password' : null))

async function submit() {
  if (isStandaloneWorkspace.value) return
  touched.value = true
  serverError.value = null
  if (emailError.value || passwordError.value) return
  isSubmitting.value = true
  try {
    await userStore.login({ email: email.value, password: password.value })
    emit('success')
  } catch (e) {
    serverError.value = e instanceof ApiError && (e.status === 401 || e.status === 400) ? 'auth.errors.invalid' : 'auth.errors.network'
  } finally {
    isSubmitting.value = false
  }
}
</script>

<template>
  <form class="login" novalidate @submit.prevent="submit">
    <div class="login__field">
      <label for="login-email" class="login__label">{{ $t('auth.email') }}</label>
      <BaseInput
        id="login-email"
        v-model="email"
        type="email"
        autocomplete="username"
        :placeholder="$t('auth.emailPlaceholder')"
        :disabled="isSubmitting || isStandaloneWorkspace"
        :aria-invalid="!!emailError"
        :aria-describedby="emailError ? 'login-email-error' : undefined"
      />
      <p v-if="emailError" id="login-email-error" class="login__error">{{ $t(emailError) }}</p>
    </div>

    <div class="login__field">
      <label for="login-password" class="login__label">{{ $t('auth.password') }}</label>
      <div class="login__password">
        <BaseInput
          id="login-password"
          v-model="password"
          :type="showPassword ? 'text' : 'password'"
          autocomplete="current-password"
          :disabled="isSubmitting || isStandaloneWorkspace"
          :aria-invalid="!!passwordError"
          :aria-describedby="passwordError ? 'login-password-error' : undefined"
        />
        <button
          type="button"
          class="login__eye"
          :aria-label="showPassword ? $t('auth.hidePassword') : $t('auth.showPassword')"
          :aria-pressed="showPassword"
          @click="showPassword = !showPassword"
        >
          <BaseIcon :name="showPassword ? 'eye-off' : 'eye'" :size="18" />
        </button>
      </div>
      <p v-if="passwordError" id="login-password-error" class="login__error">{{ $t(passwordError) }}</p>
    </div>

    <p v-if="serverError" class="login__server-error" role="alert">
      <BaseIcon name="alert" :size="16" />
      {{ $t(serverError) }}
    </p>

    <BaseButton type="submit" size="lg" block :loading="isSubmitting" :disabled="isStandaloneWorkspace">{{ $t('auth.submit') }}</BaseButton>

    <p v-if="USE_MOCK_API" class="login__demo">{{ $t('auth.demo') }}</p>
  </form>
</template>

<style scoped>
.login {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.login__field {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.login__label {
  font-size: 14px;
  font-weight: 500;
}

.login__field :deep(.input) {
  height: 48px;
  font-size: 15px;
}

.login__field :deep(.input[aria-invalid='true']) {
  border-color: rgb(255 122 122 / 0.6);
}

.login__password {
  position: relative;
}

.login__password :deep(.input) {
  padding-right: 48px;
}

.login__eye {
  position: absolute;
  top: 50%;
  right: 6px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: var(--radius-sm);
  color: var(--color-text-muted);
  transform: translateY(-50%);
  transition: color var(--transition);
}

.login__eye:hover {
  color: var(--color-text);
}

.login__error {
  color: var(--color-danger);
  font-size: 13px;
}

.login__server-error {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;
  border-radius: var(--radius-md);
  background: rgb(255 77 77 / 0.1);
  color: #ff7a7a;
  font-size: 13px;
}

.login__demo {
  color: var(--color-text-subtle);
  font-size: 12px;
  text-align: center;
}
</style>
