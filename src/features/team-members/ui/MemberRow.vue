<script setup lang="ts">
import { computed, ref } from 'vue'
import { getRoleLabel, getTeamRoleOptions, canManageMember, useTeamStore, type TeamMember, type TeamRole } from '@/entities/team'
import { UserAvatar } from '@/entities/user'
import { formatDayMonth } from '@/shared/lib'
import { BaseButton, BaseDialog, BaseSelect } from '@/shared/ui'

const props = defineProps<{
  member: TeamMember
  canManage: boolean
  pending?: boolean
}>()

const emit = defineEmits<{ setRole: [id: string, role: TeamRole]; remove: [id: string] }>()

const teamStore = useTeamStore()
const confirmOpen = ref(false)

const editable = computed(() => props.canManage && !!teamStore.currentTeam && canManageMember(teamStore.currentTeam.myRole, props.member.role, props.member.isYou))

const role = computed<TeamRole | null>({
  get: () => (props.member.role === 'owner' ? null : props.member.role),
  set: (value) => {
    if (value && value !== props.member.role) emit('setRole', props.member.id, value)
  },
})

const joined = computed(() => formatDayMonth(props.member.joinedAt))

function confirmRemove() {
  confirmOpen.value = false
  emit('remove', props.member.id)
}
</script>

<template>
  <li class="member" :class="{ 'is-pending': pending }">
    <UserAvatar :name="member.name" :src="member.avatarUrl" :size="40" />
    <div class="member__info">
      <span class="member__name">
        {{ member.name }}
        <span v-if="member.isYou" class="member__you">{{ $t('common.you') }}</span>
      </span>
      <span class="member__email">{{ member.email }}</span>
    </div>
    <span class="member__joined">{{ $t('members.since', { date: joined }) }}</span>

    <div class="member__role">
      <BaseSelect
        v-if="editable"
        v-model="role"
        :options="getTeamRoleOptions(teamStore.currentTeam?.myRole)"
        size="sm"
        :aria-label="$t('members.roleFor', { name: member.name })"
        :disabled="pending"
      />
      <span v-else class="member__role-text">{{ getRoleLabel(member.role) }}</span>
    </div>

    <div class="member__action">
      <button
        v-if="editable"
        type="button"
        class="member__remove"
        :disabled="pending"
        @click="confirmOpen = true"
      >
        {{ $t('members.remove') }}
      </button>
    </div>

    <BaseDialog v-model:open="confirmOpen" :title="$t('members.removeTitle', { name: member.name })">
      <p>{{ $t('members.removeText') }}</p>
      <template #actions>
        <BaseButton variant="tonal" @click="confirmOpen = false">{{ $t('common.cancel') }}</BaseButton>
        <BaseButton variant="danger" @click="confirmRemove">{{ $t('members.remove') }}</BaseButton>
      </template>
    </BaseDialog>
  </li>
</template>

<style scoped>
.member {
  display: grid;
  grid-template-columns: 40px minmax(0, 1fr) 170px 200px 90px;
  align-items: center;
  gap: 16px;
  min-height: 76px;
  padding: 12px 20px;
  border-top: 1px solid var(--color-border);
  transition: opacity var(--transition);
}

.member.is-pending {
  opacity: 0.6;
}

.member__info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.member__name {
  overflow: hidden;
  font-size: 15px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.member__you {
  margin-left: 6px;
  padding: 2px 8px;
  border-radius: var(--radius-pill);
  background: var(--color-surface-raised);
  color: var(--color-text-muted);
  font-size: 11px;
  font-weight: 400;
}

.member__email {
  overflow: hidden;
  color: var(--color-text-muted);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.member__joined {
  color: var(--color-text-subtle);
  font-size: 13px;
  white-space: nowrap;
}

.member__role-text {
  padding-left: 14px;
  color: var(--color-text-secondary);
  font-size: 14px;
}

.member__action {
  text-align: right;
}

.member__remove {
  color: #ff5c5c;
  font-size: 14px;
  transition: opacity var(--transition);
}

.member__remove:hover:not(:disabled) {
  opacity: 0.8;
}

@media (max-width: 760px) {
  .member {
    grid-template-columns: 40px minmax(0, 1fr) auto;
  }

  .member__joined {
    display: none;
  }

  .member__role {
    grid-column: 2 / 3;
  }

  .member__action {
    grid-column: 3;
  }
}
</style>
