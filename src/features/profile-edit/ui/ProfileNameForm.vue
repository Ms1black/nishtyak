<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useUserStore } from '@/entities/user'
import { BaseButton, BaseInput } from '@/shared/ui'

const userStore = useUserStore()
const name = ref(userStore.currentUser.name)
const isSaving = ref(false)
const status = ref<'idle' | 'saved' | 'error'>('idle')

watch(
  () => userStore.currentUser.name,
  (value) => (name.value = value),
)

const isChanged = computed(() => name.value.trim() !== userStore.currentUser.name)
const isValid = computed(() => name.value.trim().length >= 2)

async function save() {
  if (!isChanged.value || !isValid.value) return
  isSaving.value = true
  status.value = 'idle'
  try {
    await userStore.updateName(name.value.trim())
    status.value = 'saved'
  } catch {
    status.value = 'error'
  } finally {
    isSaving.value = false
  }
}
</script>

<template>
  <form class="name-form" novalidate @submit.prevent="save">
    <label for="profile-name" class="name-form__label">{{ $t('profile.name') }}</label>
    <BaseInput id="profile-name" v-model="name" :maxlength="80" :disabled="isSaving" @input="status = 'idle'" />
    <div class="name-form__footer">
      <p class="name-form__status" :class="{ 'is-error': status === 'error' }" aria-live="polite">
        {{
          status === 'saved'
            ? $t('common.saved')
            : status === 'error'
              ? $t('profile.saveError')
              : !isValid
                ? $t('profile.nameMin')
                : $t('profile.nameHint')
        }}
      </p>
      <BaseButton type="submit" size="sm" :disabled="!isChanged || !isValid" :loading="isSaving">{{ $t('common.save') }}</BaseButton>
    </div>
  </form>
</template>

<style scoped>
.name-form__label {
  display: block;
  margin-bottom: 10px;
  font-size: 14px;
  font-weight: 500;
}

.name-form__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 12px;
}

.name-form__status {
  color: var(--color-text-muted);
  font-size: 13px;
}

.name-form__status.is-error {
  color: var(--color-danger);
}
</style>
