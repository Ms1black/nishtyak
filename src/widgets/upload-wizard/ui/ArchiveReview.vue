<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { VerificationStatus } from '@/entities/project'
import { sessionGeneration } from '@/shared/api'
import { BaseButton, BaseCard, BaseInput, BaseTextarea } from '@/shared/ui'
import { ArchiveReview, readmeCommands } from '../model/archiveReview'

const props = defineProps<{ projectId: string; archiveTag?: string; canEdit: boolean }>()
const emit = defineEmits<{ refresh: [] }>()
const review = new ArchiveReview()
const state = review.state
const copyStatus = ref<'copied' | 'error' | null>(null)
const commands = computed(() => readmeCommands(state.readme))
const statuses: VerificationStatus[] = ['not_checked', 'passed', 'failed', 'unable']
const canSave = computed(() => props.canEdit && !!state.verification?.analysisId &&
  !!(props.archiveTag ?? state.verification?.archiveTag) && !state.verificationLoading && !state.saving)

function load() { void review.open(props.projectId, props.archiveTag) }
onMounted(load)
watch(() => [props.projectId, props.archiveTag], load)
watch(sessionGeneration, () => review.reset(), { flush: 'sync' })
onBeforeUnmount(() => review.reset())

async function copy(content: string) {
  copyStatus.value = null
  try {
    await navigator.clipboard.writeText(content)
    copyStatus.value = 'copied'
  } catch { copyStatus.value = 'error' }
}
</script>

<template>
  <BaseCard class="archive-review" padding="lg">
    <h2 class="step-title">{{ $t('ready.repeat.title') }}</h2>
    <p class="step-muted">{{ $t('ready.repeat.hint') }}</p>
    <p v-if="state.readmeLoading" role="status">{{ $t('ready.readme.loading') }}</p>
    <div v-else-if="state.readmeError" class="archive-review__notice" role="alert">
      <p>{{ $t(state.readmeError) }}</p>
      <BaseButton variant="tonal" @click="review.loadReadme()">{{ $t('common.retry') }}</BaseButton>
    </div>
    <template v-else-if="state.readme">
      <div v-for="(command, index) in commands" :key="index" class="archive-review__command">
        <h3>{{ command.title || $t('ready.readme.commands') }}</h3>
        <pre><code>{{ command.content }}</code></pre>
        <BaseButton size="sm" variant="tonal" @click="copy(command.content)">{{ $t('ready.readme.copyCommands') }}</BaseButton>
      </div>
      <details class="archive-review__readme">
        <summary>{{ $t('ready.readme.preview') }}</summary>
        <pre>{{ state.readme }}</pre>
        <BaseButton size="sm" variant="tonal" @click="copy(state.readme)">{{ $t('ready.readme.copyReadme') }}</BaseButton>
      </details>
      <p v-if="copyStatus" role="status">{{ $t(copyStatus === 'copied' ? 'ready.readme.copied' : 'ready.readme.copyError') }}</p>
    </template>

    <form class="archive-review__verification" @submit.prevent="review.save(canEdit)">
      <h2 class="step-title">{{ $t('ready.verification.title') }}</h2>
      <p class="step-muted">{{ $t('ready.verification.hint') }}</p>
      <p v-if="state.verificationLoading" role="status">{{ $t('ready.verification.loading') }}</p>
      <p v-if="state.verification?.stale" class="archive-review__notice" role="note">{{ $t('ready.verification.stale') }}</p>
      <p v-if="state.verification?.verification" class="step-muted">
        {{ $t('ready.verification.recordedBy', { name: state.verification.verification.checkedBy.name, date: state.verification.verification.checkedAt }) }}
      </p>
      <p v-if="!canEdit" class="step-muted">{{ $t('ready.verification.readOnly') }}</p>
      <fieldset :disabled="!canEdit || state.verificationLoading || state.saving || !state.verification">
        <label class="archive-review__field">
          <span>{{ $t('ready.verification.status') }}</span>
          <select v-model="state.form.status">
            <option v-for="status in statuses" :key="status" :value="status">{{ $t(`ready.verification.statuses.${status}`) }}</option>
          </select>
        </label>
        <div class="archive-review__row">
          <label class="archive-review__field">
            <span>Python</span>
            <BaseInput v-model="state.form.pythonVersion" :maxlength="100" />
          </label>
          <label class="archive-review__field">
            <span>{{ $t('ready.verification.platform') }}</span>
            <BaseInput v-model="state.form.platform" :maxlength="100" />
          </label>
        </div>
        <label class="archive-review__field">
          <span>{{ $t('ready.verification.command') }}</span>
          <BaseInput v-model="state.form.command" mono :maxlength="2000" />
        </label>
        <label class="archive-review__field">
          <span>{{ $t('ready.verification.output') }}</span>
          <BaseTextarea v-model="state.form.output" :maxlength="12000" :rows="3" />
        </label>
        <label class="archive-review__field">
          <span>{{ $t('ready.verification.notes') }}</span>
          <BaseTextarea v-model="state.form.notes" :maxlength="6000" :rows="2" />
        </label>
        <BaseButton v-if="canEdit" type="submit" variant="tonal" :disabled="!canSave" :loading="state.saving">{{ $t('ready.verification.save') }}</BaseButton>
      </fieldset>
      <p v-if="state.saved" role="status">{{ $t('ready.verification.saved') }}</p>
      <div v-if="state.verificationError" class="archive-review__notice" role="alert">
        <p>{{ $t(state.verificationError) }}</p>
        <BaseButton v-if="state.verificationConflict" variant="tonal" :disabled="state.saving" @click="emit('refresh')">{{ $t('ready.verification.reload') }}</BaseButton>
        <BaseButton v-else-if="state.verification" variant="tonal" :disabled="state.saving || !canEdit" @click="review.save(canEdit)">{{ $t('ready.verification.retrySave') }}</BaseButton>
        <BaseButton v-else variant="tonal" :disabled="state.saving" @click="review.loadVerification()">{{ $t('common.retry') }}</BaseButton>
      </div>
    </form>
  </BaseCard>
</template>

<style scoped src="./step.css"></style>
<style scoped>
.archive-review,
.archive-review__verification,
.archive-review fieldset,
.archive-review__field,
.archive-review__command,
.archive-review__notice {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
}
.archive-review { grid-column: 1 / -1; }
.archive-review fieldset { padding: 0; border: 0; }
.archive-review__verification { padding-top: 24px; border-top: 1px solid var(--color-border); }
.archive-review__field { gap: 8px; font-size: 14px; }
.archive-review__row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.archive-review pre {
  margin: 12px 0;
  padding: 16px;
  max-height: 480px;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  border-radius: var(--radius-md);
  background: var(--color-bg);
  font-size: 13px;
  line-height: 1.6;
}
.archive-review h3 { font-size: 15px; font-weight: 500; }
.archive-review summary { cursor: pointer; }
.archive-review select { padding: 14px; background: var(--color-bg); border-radius: var(--radius-md); }
.archive-review .btn { align-self: flex-start; }
@media (max-width: 640px) { .archive-review__row { grid-template-columns: 1fr; } }
</style>
