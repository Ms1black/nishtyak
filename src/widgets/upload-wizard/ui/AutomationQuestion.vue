<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { formatRunCommand, type AnalysisDecisions, type AutomationQuestion, type LaunchCandidate } from '@/entities/project'
import { BaseButton, BaseInput } from '@/shared/ui'

const props = defineProps<{
  question: AutomationQuestion
  candidates: LaunchCandidate[]
  disabled?: boolean
}>()

const emit = defineEmits<{
  answer: [changes: Partial<AnalysisDecisions>]
  openSettings: []
}>()

const value = ref('')
watch(() => props.question.id, () => { value.value = '' })

const choices = computed(() => {
  const allowed = new Set(props.question.candidateIds ?? [])
  return props.candidates.filter((candidate) => allowed.has(candidate.id))
})
const canAnswerText = computed(() =>
  props.question.kind === 'package_mapping' && !!props.question.module ||
  props.question.kind === 'version_constraint' && !!props.question.declarationId,
)

function submit() {
  const answer = value.value.trim()
  if (props.disabled || !answer) return
  if (props.question.kind === 'package_mapping' && props.question.module) {
    emit('answer', { packages: { [props.question.module]: answer } })
  } else if (props.question.kind === 'version_constraint' && props.question.declarationId) {
    emit('answer', {
      declarationActions: { [props.question.declarationId]: { action: 'replace', requirement: answer } },
    })
  }
}
</script>

<template>
  <div class="question">
    <h3 class="question__title">
      {{ $t(`analysis.automation.questions.${question.kind}`, { module: question.module ?? '' }) }}
    </h3>
    <p class="question__hint">{{ $t('analysis.automation.questionHint') }}</p>
    <p v-if="question.message" class="question__hint">{{ question.message }}</p>
    <form v-if="canAnswerText" class="question__form" @submit.prevent="submit">
      <BaseInput
        v-model="value"
        mono
        :disabled="disabled"
        :placeholder="question.kind === 'package_mapping' ? 'scikit-learn' : 'numpy>=1.26'"
        :aria-label="$t(`analysis.automation.questions.${question.kind}`, { module: question.module ?? '' })"
      />
      <BaseButton type="submit" :disabled="disabled || !value.trim()">
        {{ $t('analysis.automation.continue') }}
      </BaseButton>
    </form>
    <div v-else-if="question.kind === 'entrypoint' && choices.length" class="question__choices">
      <button
        v-for="candidate in choices"
        :key="candidate.id"
        type="button"
        class="question__candidate"
        :disabled="disabled"
        @click="emit('answer', { runSpec: { ...candidate.runSpec, args: [...candidate.runSpec.args] } })"
      >
        <code>{{ formatRunCommand(candidate.runSpec) }}</code>
      </button>
    </div>
    <BaseButton v-else variant="tonal" :disabled="disabled" @click="emit('openSettings')">
      {{ $t('analysis.automation.openSettings') }}
    </BaseButton>
  </div>
</template>

<style scoped>
.question,
.question__choices {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.question__title {
  font-family: var(--font-display);
  font-size: 20px;
  font-weight: 500;
}
.question__hint {
  color: var(--color-text-muted);
  font-size: 13px;
  line-height: 1.5;
}
.question__form {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}
.question__form > input {
  flex: 1 1 200px;
}
.question__candidate {
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  font-size: 14px;
  text-align: left;
  overflow-wrap: anywhere;
}
.question__candidate:hover:not(:disabled) {
  background: var(--color-bg);
}
</style>
