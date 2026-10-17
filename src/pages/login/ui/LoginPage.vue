<script setup lang="ts">
import { computed } from 'vue'
import { safeAuthRedirect } from '@/shared/lib/authRedirect'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { LoginForm } from '@/features/auth-login'
import { RouteName } from '@/shared/config'
import { BaseCard } from '@/shared/ui'
import { isStandaloneWorkspace } from '@/shared/api'

const route = useRoute()
const router = useRouter()
const register = computed(() => route.name === RouteName.Register)
const redirect = computed(() => safeAuthRedirect(route.query.redirect))

function onSuccess() {
  router.replace(redirect.value)
}
</script>

<template>
  <div class="container login-page">
    <BaseCard tag="section" padding="lg" class="login-page__card" aria-labelledby="login-title">
      <h1 id="login-title" class="login-page__title">{{ $t(register ? 'auth.registerTitle' : 'auth.title') }}</h1>
      <LoginForm :key="String(register)" :register="register" class="login-page__form" @success="onSuccess" />
      <p v-if="!isStandaloneWorkspace" class="login-page__preview">
        {{ $t(register ? 'auth.haveAccount' : 'auth.noAccount') }}
        <RouterLink :to="{ name: register ? RouteName.Login : RouteName.Register, query: { redirect } }">{{ $t(register ? 'auth.submit' : 'auth.registerSubmit') }}</RouterLink>
      </p>
      <p v-if="isStandaloneWorkspace" class="login-page__preview">
        {{ $t('integration.authPending') }}
        <RouterLink :to="{ name: RouteName.Upload }">{{ $t('integration.openWorkspace') }}</RouterLink>
      </p>
    </BaseCard>
    <p class="login-page__terms">
      {{ $t('auth.agree') }}
      <RouterLink :to="{ name: RouteName.Terms }">{{ $t('auth.agreeLink') }}</RouterLink>
    </p>
  </div>
</template>

<style scoped>
.login-page {
  display: flex;
  flex: 1;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}

.login-page__card {
  width: min(440px, 100%);
}

.login-page__title {
  font-family: var(--font-display);
  font-size: 30px;
  font-weight: 500;
  line-height: 1.2;
}

.login-page__form {
  margin-top: 32px;
}

.login-page__preview {
  margin-top: 20px;
  color: var(--color-text-muted);
  font-size: 13px;
  line-height: 1.5;
}

.login-page__preview a {
  display: block;
  margin-top: 8px;
  color: var(--color-text);
  text-decoration: underline;
}

.login-page__terms {
  margin-top: 20px;
  color: var(--color-text-subtle);
  font-size: 13px;
  text-align: center;
}

.login-page__terms a {
  color: var(--color-text-secondary);
  text-decoration: underline;
  text-decoration-color: var(--color-text-subtle);
  text-underline-offset: 3px;
}

.login-page__terms a:hover {
  color: var(--color-text);
}
</style>
