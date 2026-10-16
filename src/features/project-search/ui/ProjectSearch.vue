<script setup lang="ts">
import { BaseIcon } from '@/shared/ui'

const query = defineModel<string>({ required: true })
</script>

<template>
  <label class="search">
    <BaseIcon name="search" :size="20" class="search__icon" />
    <span class="visually-hidden">{{ $t('projects.searchLabel') }}</span>
    <input v-model="query" type="search" class="search__input" :placeholder="$t('projects.searchPlaceholder')" />
    <button v-if="query" type="button" class="search__clear" :aria-label="$t('common.clearSearch')" @click="query = ''">
      <BaseIcon name="close" :size="16" />
    </button>
  </label>
</template>

<style scoped>
.search {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 300px;
  max-width: 100%;
  height: 46px;
  padding: 0 16px;
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-pill);
  transition: border-color var(--transition);
}

.search:focus-within {
  border-color: var(--color-text-muted);
}

.search__icon {
  color: var(--color-text-secondary);
}

.search__input {
  flex: 1;
  min-width: 0;
  border: 0;
  outline: none;
  background: transparent;
  font-size: 14px;
}

.search__input::placeholder {
  color: var(--color-text-muted);
}

.search__input::-webkit-search-cancel-button {
  display: none;
}

.search__clear {
  display: inline-flex;
  color: var(--color-text-muted);
}

.search__clear:hover {
  color: var(--color-text);
}
</style>
