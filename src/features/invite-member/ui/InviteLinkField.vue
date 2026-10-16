<script setup lang="ts">
import { useClipboard } from '@/shared/lib'
import { BaseButton } from '@/shared/ui'

const props = defineProps<{
  url: string | null
  loading?: boolean
  regenerating?: boolean
  disabled?: boolean
}>()

const emit = defineEmits<{ regenerate: [] }>()

const { copied, copy } = useClipboard()

function onCopy() {
  if (props.url) copy(props.url)
}
</script>

<template>
  <div class="invite-link">
    <div class="invite-link__field">
      <span id="invite-link-label" class="invite-link__label">{{ $t('invite.linkLabel') }}</span>
      <input
        class="invite-link__value"
        :class="{ 'is-loading': loading || regenerating }"
        :value="url ?? ''"
        :placeholder="$t('invite.createHint')"
        aria-labelledby="invite-link-label"
        readonly
        @focus="($event.target as HTMLInputElement).select()"
      />
    </div>
    <div class="invite-link__actions">
      <BaseButton variant="primary" size="sm" :icon="copied ? 'check' : 'copy'" :disabled="!url" block @click="onCopy">
        {{ copied ? $t('common.copied') : $t('common.copy') }}
      </BaseButton>
      <BaseButton
        variant="secondary"
        size="sm"
        icon="refresh"
        :loading="regenerating"
        :disabled="loading || disabled"
        block
        :title="$t('invite.rotateTitle')"
        @click="emit('regenerate')"
      >
        {{ $t('common.refresh') }}
      </BaseButton>
    </div>
    <span class="visually-hidden" aria-live="polite">{{ copied ? $t('invite.linkCopied') : '' }}</span>
  </div>
</template>

<style scoped>
.invite-link {
  display: flex;
  align-items: center;
  gap: 12px;
}

.invite-link__field {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  padding: 12px 16px;
  border-radius: var(--radius-sm);
  background: var(--color-surface-raised);
}

.invite-link__label {
  color: var(--color-text-muted);
  font-size: 12px;
}

.invite-link__value {
  width: 100%;
  padding: 0;
  border: 0;
  outline: none;
  background: transparent;
  font-size: 14px;
  text-overflow: ellipsis;
  transition: opacity var(--transition);
}

.invite-link__value.is-loading {
  opacity: 0.4;
}

.invite-link__actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 160px;
  flex-shrink: 0;
}

@media (max-width: 640px) {
  .invite-link {
    flex-direction: column;
    align-items: stretch;
  }

  .invite-link__actions {
    flex-direction: row;
    width: 100%;
  }
}
</style>
