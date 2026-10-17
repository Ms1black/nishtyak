<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { formatRunCommand, type AutomationQuestion, type LaunchCandidate } from '@/entities/project'
import { BaseButton, BaseInput, BaseTextarea } from '@/shared/ui'
import { collectClarificationChanges, type ClarificationAnswers } from '../model/clarifications'

const props = defineProps<{
  questions: AutomationQuestion[]
  questionnaireId?: string | null
  candidates: LaunchCandidate[]
  disabled?: boolean
}>()

const emit = defineEmits<{
  answer: [answers: ClarificationAnswers]
  openSettings: []
}>()

const values = ref<Record<string, string>>({})
const notes = ref('')
watch(() => props.questionnaireId, () => {
  values.value = Object.fromEntries(props.questions.map((question) => [question.id, '']))
  notes.value = ''
}, { immediate: true })

function choices(question: AutomationQuestion) {
  const allowed = new Set(question.candidateIds ?? [])
  return props.candidates.filter((candidate) => allowed.has(candidate.id))
}

function hasTextField(question: AutomationQuestion) {
  return question.kind === 'package_mapping' && !!question.module ||
    question.kind === 'version_constraint' && !!(question.declarationId || question.module)
}

const hasUnboundQuestions = computed(() => props.questions.some((question) =>
  !hasTextField(question) && !(question.kind === 'entrypoint' && choices(question).length),
))

function submit() {
  if (props.disabled || !props.questionnaireId) return
  emit('answer', {
    changes: collectClarificationChanges(props.questions, values.value, props.candidates),
    notes: notes.value.trim(),
  })
}
</script>

<template>
  <form class="question" @submit.prevent="submit">
    <h3 class="question__title">{{ $t('analysis.automation.batchTitle') }}</h3>
    <p class="question__hint">{{ $t('analysis.automation.questionHint') }}</p>
    <fieldset v-for="question in questions" :key="question.id" class="question__item" :disabled="disabled">
      <legend class="question__label">
        {{ $t(`analysis.automation.questions.${question.kind}`, { module: question.module ?? '' }) }}
      </legend>
      <p v-if="question.message" class="question__hint">{{ question.message }}</p>
      <template v-if="hasTextField(question)">
        <label :for="`answer-${question.id}`" class="question__hint">
          {{ $t(question.kind === 'package_mapping' ? 'analysis.automation.packageHint' : question.declarationId ? 'analysis.automation.requirementHint' : 'analysis.automation.versionHint', { module: question.module ?? '' }) }}
        </label>
        <BaseInput
          :id="`answer-${question.id}`"
          :model-value="values[question.id] ?? ''"
          @update:model-value="values[question.id] = $event"
          mono
          :disabled="disabled"
          :maxlength="2000"
          :placeholder="question.kind === 'package_mapping' ? 'scikit-learn' : question.declarationId ? 'numpy>=1.26' : '>=1.26'"
        />
      </template>
      <div v-else-if="question.kind === 'entrypoint' && choices(question).length" class="question__choices">
        <label class="question__candidate">
          <input v-model="values[question.id]" type="radio" :name="`launch-${question.id}`" value="" :disabled="disabled">
          {{ $t('analysis.automation.unknown') }}
        </label>
        <label v-for="candidate in choices(question)" :key="candidate.id" class="question__candidate">
          <input v-model="values[question.id]" type="radio" :name="`launch-${question.id}`" :value="candidate.id" :disabled="disabled">
          <code>{{ formatRunCommand(candidate.runSpec) }}</code>
        </label>
      </div>
      <p v-else class="question__hint">{{ $t('analysis.automation.textHint') }}</p>
    </fieldset>
    <label for="clarification-notes" class="question__label">{{ $t('analysis.automation.notesLabel') }}</label>
    <BaseTextarea id="clarification-notes" v-model="notes" :maxlength="6000" :rows="3" :disabled="disabled" />
    <BaseButton v-if="hasUnboundQuestions" variant="tonal" :disabled="disabled" @click="emit('openSettings')">
      {{ $t('analysis.automation.openSettings') }}
    </BaseButton>
    <p class="question__hint">{{ $t('analysis.automation.finalPassHint') }}</p>
    <BaseButton type="submit" :disabled="disabled || !questionnaireId">{{ $t('analysis.automation.continue') }}</BaseButton>
  </form>
</template>

<style scoped>
.question,
.question__choices,
.question__item {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.question__item {
  min-width: 0;
  padding: 20px 0;
  border: 0;
  border-top: 1px solid var(--color-border);
}
.question__label {
  font-size: 15px;
  font-weight: 500;
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
.question__candidate {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  font-size: 14px;
  text-align: left;
  overflow-wrap: anywhere;
}
.question__candidate:hover {
  background: var(--color-bg);
}
</style>
