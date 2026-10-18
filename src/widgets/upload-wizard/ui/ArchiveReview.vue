<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { VerificationStatus } from '@/entities/project'
import { sessionGeneration } from '@/shared/api'
import { BaseButton, BaseCard, BaseInput, BaseTextarea, CodeBlock } from '@/shared/ui'
import { ArchiveReview, readmeCommands } from '../model/archiveReview'

const props = defineProps<{ projectId: string; archiveTag?: string; canEdit: boolean }>()
const emit = defineEmits<{ refresh: []; 'open-file': [path: string] }>()
const review = new ArchiveReview()
const state = review.state
const verificationDetailsOpen = ref(false)
const canPreviewReadme = computed(() => /^[a-f0-9]{64}$/.test(props.archiveTag ?? ''))
const commands = computed(() => readmeCommands(state.readme))
const statuses: VerificationStatus[] = ['not_checked', 'passed', 'failed', 'unable']
const canSave = computed(() => props.canEdit && !!state.verification?.analysisId &&
  !!(props.archiveTag ?? state.verification?.archiveTag) && !state.verificationLoading && !state.saving)

function load() { void review.open(props.projectId, props.archiveTag) }
onMounted(load)
watch(() => [props.projectId, props.archiveTag], () => {
  verificationDetailsOpen.value = false
  load()
})
watch(sessionGeneration, () => review.reset(), { flush: 'sync' })
onBeforeUnmount(() => review.reset())

function openReadme() {
  if (canPreviewReadme.value) emit('open-file', 'README.md')
}

function toggleVerificationDetails(event: Event) {
  verificationDetailsOpen.value = (event.currentTarget as HTMLDetailsElement).open
}
</script>

<template>
  <BaseCard class="archive-review" padding="lg">
    <h2 class="step-title">{{ $t('ready.repeat.title') }}</h2>
    <p class="step-muted">{{ $t('ready.repeat.hint') }}</p>
    <BaseButton variant="tonal" :disabled="!canPreviewReadme" @click="openReadme">{{ $t('ready.readme.preview') }}</BaseButton>
    <p v-if="state.readmeLoading" role="status">{{ $t('ready.readme.loading') }}</p>
    <div v-else-if="state.readmeError" class="archive-review__notice" role="alert">
      <p>{{ $t(state.readmeError) }}</p>
      <BaseButton variant="tonal" @click="review.loadReadme()">{{ $t('common.retry') }}</BaseButton>
    </div>
    <template v-else-if="state.readme">
      <div v-for="(command, index) in commands" :key="index" class="archive-review__command">
        <h3>{{ command.title || $t('ready.readme.commands') }}</h3>
        <CodeBlock :code="command.content" :language="command.language" :line-numbers="false" />
      </div>
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
        <label class="archive-review__field">
          <span>{{ $t('ready.verification.notes') }}</span>
          <BaseTextarea v-model="state.form.notes" :maxlength="6000" :rows="2" />
        </label>
        <details class="archive-review__details" :open="verificationDetailsOpen" @toggle="toggleVerificationDetails">
          <summary>{{ $t('ready.verification.details') }}</summary>
          <div class="archive-review__details-body">
            <p class="step-muted">{{ $t('ready.verification.detailsHint') }}</p>
            <div class="archive-review__row">
              <label class="archive-review__field">
                <span>{{ $t('ready.verification.pythonVersion') }}</span>
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
          </div>
        </details>
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
.archive-review__notice,
.archive-review__details-body {
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
.archive-review__details-body { margin-top: 14px; }
.archive-review h3 { font-size: 15px; font-weight: 500; }
.archive-review summary { cursor: pointer; }
.archive-review select { padding: 14px; background: var(--color-bg); border-radius: var(--radius-md); }
.archive-review .btn { align-self: flex-start; }
@media (max-width: 640px) { .archive-review__row { grid-template-columns: 1fr; } }
</style>
