<script setup lang="ts">
import { computed, ref } from 'vue'
import { useTeamStore, type Team } from '@/entities/team'
import { BaseButton, BaseDialog, BaseInput } from '@/shared/ui'

const props = defineProps<{ team: Team }>()

const teamStore = useTeamStore()
const open = ref(false)
const confirmName = ref('')
const isDeleting = ref(false)
const error = ref<string | null>(null)

const isLast = computed(() => teamStore.teams.length <= 1)
const matches = computed(() => confirmName.value.trim() === props.team.name)

function start() {
  confirmName.value = ''
  error.value = null
  open.value = true
}

async function confirm() {
  if (!matches.value) return
  isDeleting.value = true
  error.value = null
  try {
    await teamStore.deleteTeam(props.team.id)
    open.value = false
  } catch {
    error.value = 'teams.delete.error'
  } finally {
    isDeleting.value = false
  }
}
</script>

<template>
  <button
    type="button"
    class="delete"
    :disabled="isLast"
    :title="isLast ? $t('teams.delete.lastTeam') : undefined"
    @click="start"
  >
    {{ $t('common.remove') }}
  </button>

  <BaseDialog v-model:open="open" :title="$t('teams.delete.title', { name: team.name })">
    <p>
      {{ $t('teams.delete.text') }}
      {{ team.membersCount > 1 ? $t('teams.delete.membersLose', team.membersCount - 1) : '' }}
    </p>
    <label class="confirm">
      <span>{{ $t('teams.delete.confirm') }}</span>
      <BaseInput v-model="confirmName" :placeholder="team.name" @keydown.enter.prevent="confirm" />
    </label>
    <p v-if="error" class="error" role="alert">{{ $t(error) }}</p>
    <template #actions>
      <BaseButton variant="tonal" :disabled="isDeleting" @click="open = false">{{ $t('common.cancel') }}</BaseButton>
      <BaseButton variant="danger" :disabled="!matches" :loading="isDeleting" @click="confirm">
        {{ $t('teams.delete.submit') }}
      </BaseButton>
    </template>
  </BaseDialog>
</template>

<style scoped>
.delete {
  color: #ff5c5c;
  font-size: 14px;
  white-space: nowrap;
  transition: opacity var(--transition);
}

.delete:hover:not(:disabled) {
  opacity: 0.8;
}

.delete:disabled {
  color: var(--color-text-subtle);
  cursor: not-allowed;
}

.confirm {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 20px;
  color: var(--color-text);
  font-size: 13px;
}

.error {
  margin-top: 12px;
  color: var(--color-danger);
}
</style>
