<script setup lang="ts">
import { RouterLink } from 'vue-router'
import { RouteName } from '@/shared/config'

defineProps<{ note?: string }>()
</script>

<template>
  <footer class="footer">
    <p class="footer__right">
      <span>{{ note ? $t(note) : $t('app.credits') }}</span>
      <span aria-hidden="true">·</span>
      <RouterLink :to="{ name: RouteName.Terms }" class="footer__link">{{ $t('app.terms') }}</RouterLink>
    </p>
  </footer>
</template>

<style scoped>
.footer {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 12px 24px;
  padding-block: 32px;
  border-top: 1px solid var(--color-border);
  color: var(--color-text-muted);
  font-size: 12px;
}

.footer__right {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
}

.footer__link {
  transition: color var(--transition);
}

.footer__link:hover,
.footer__link.router-link-active {
  color: var(--color-text);
}
</style>
