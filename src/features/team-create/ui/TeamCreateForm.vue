<script setup lang="ts">
import { ref } from 'vue'
import { useTeamStore } from '@/entities/team'
import { BaseButton, BaseInput } from '@/shared/ui'

const MAX_LENGTH = 64

const teamStore = useTeamStore()
const name = ref('')
const isCreating = ref(false)
const error = ref<string | null>(null)
const root = ref<HTMLElement | null>(null)

const emit = defineEmits<{ created: [name: string] }>()

async function submit() {
  const value = name.value.trim()
  if (value.length < 2) {
    error.value = 'teams.create.tooShort'
    return
  }
  if (teamStore.teams.some((t) => t.name.toLowerCase() === value.toLowerCase())) {
    error.value = 'teams.create.duplicate'
    return
  }
  isCreating.value = true
  error.value = null
  try {
    await teamStore.createTeam(value)
    name.value = ''
    emit('created', value)
  } catch {
    error.value = 'teams.create.error'
  } finally {
    isCreating.value = false
  }
}

function focus() {
  root.value?.querySelector('input')?.focus()
}

defineExpose({ focus })
</script>

<template>
  <form ref="root" class="create" novalidate @submit.prevent="submit">
    <label for="team-name" class="create__label">{{ $t('teams.create.label') }}</label>
    <div class="create__row">
      <BaseInput
        id="team-name"
        v-model="name"
        :maxlength="MAX_LENGTH"
        :placeholder="$t('teams.create.placeholder')"
        :disabled="isCreating"
        @input="error = null"
      />
      <BaseButton type="submit" icon="plus" :loading="isCreating" :disabled="!name.trim()">{{ $t('teams.create.submit') }}</BaseButton>
    </div>
    <p v-if="error" class="create__error" role="alert">{{ $t(error) }}</p>
    <p v-else class="create__hint">{{ $t('teams.create.hint') }}</p>
  </form>
</template>

<style scoped>
.create__label {
  display: block;
  margin-bottom: 10px;
  font-size: 14px;
  font-weight: 500;
}

.create__row {
  display: flex;
  gap: 12px;
}

.create__row :deep(.input) {
  height: 46px;
}

.create__hint,
.create__error {
  margin-top: 10px;
  color: var(--color-text-muted);
  font-size: 13px;
}

.create__error {
  color: var(--color-danger);
}

@media (max-width: 520px) {
  .create__row {
    flex-direction: column;
  }
}
</style>
