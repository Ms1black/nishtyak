<script setup lang="ts">
import { computed } from 'vue'
import { formatRunCommand, type AutomationQuestion, type LaunchCandidate } from '@/entities/project'
import { BaseButton, BaseInput, BaseTextarea } from '@/shared/ui'
import { integrationSession } from '@/shared/api'
import { collectClarificationChanges, uniqueClarificationQuestions, type ClarificationAnswers } from '../model/clarifications'
import { useClarificationDraft } from '../model/clarificationDraft'

const props = defineProps<{
  questions: AutomationQuestion[]
  questionnaireId?: string | null
  projectId?: string
  analysisId?: string
  candidates: LaunchCandidate[]
  disabled?: boolean
}>()

const emit = defineEmits<{
  answer: [answers: ClarificationAnswers]
  openSettings: [question?: AutomationQuestion]
}>()

const visibleQuestions = computed(() => uniqueClarificationQuestions(props.questions))
const draftContext = computed(() => {
  const userId = integrationSession.value?.user.id
  if (!userId || !props.projectId || !props.analysisId || !props.questionnaireId) return null
  return { userId, projectId: props.projectId, analysisId: props.analysisId, questionnaireId: props.questionnaireId }
})
const { values, notes, draftSaved } = useClarificationDraft(draftContext, () => props.questions)

function choices(question: AutomationQuestion) {
  const allowed = new Set(question.candidateIds ?? [])
  return props.candidates.filter((candidate) => allowed.has(candidate.id))
}

function hasTextField(question: AutomationQuestion) {
  return question.kind === 'package_mapping' && !!question.module ||
    question.kind === 'version_constraint' && !!(question.declarationId || question.module)
}

const unboundQuestions = computed(() => visibleQuestions.value.filter((question) =>
  !hasTextField(question) && !(question.kind === 'entrypoint' && choices(question).length),
))
const hasPackages = computed(() => visibleQuestions.value.some((question) => question.kind === 'package_mapping'))

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
    <p v-if="hasPackages" class="question__hint">{{ $t('analysis.automation.packageHint') }}</p>
    <fieldset v-for="question in visibleQuestions" :key="question.id" class="question__item" :disabled="disabled">
      <legend class="question__label">
        {{ $t(`analysis.automation.questions.${question.kind}`, { module: question.module ?? '' }) }}
      </legend>
      <p v-if="question.message && question.kind !== 'package_mapping'" class="question__hint">{{ question.message }}</p>
      <code v-if="question.filePath" class="question__hint">{{ question.filePath }}</code>
      <template v-if="hasTextField(question)">
        <label v-if="question.kind !== 'package_mapping'" :for="`answer-${question.id}`" class="question__hint">
          {{ $t(question.declarationId ? 'analysis.automation.requirementHint' : 'analysis.automation.versionHint', { module: question.module ?? '' }) }}
        </label>
        <BaseInput
          :id="`answer-${question.id}`"
          :aria-label="$t(`analysis.automation.questions.${question.kind}`, { module: question.module ?? '' })"
          :model-value="values[question.id] ?? ''"
          @update:model-value="values[question.id] = $event"
          mono
          :disabled="disabled"
          :maxlength="2000"
          :placeholder="question.kind === 'package_mapping' ? $t('analysis.deps.packagePlaceholder') : question.declarationId ? 'numpy>=1.26' : '>=1.26'"
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
      <BaseButton v-else-if="question.code || question.filePath" variant="tonal" :disabled="disabled" @click="emit('openSettings', question)">
        {{ $t('analysis.automation.openIssue') }}
      </BaseButton>
    </fieldset>
    <label for="clarification-notes" class="question__label">{{ $t('analysis.automation.notesLabel') }}</label>
    <BaseTextarea id="clarification-notes" v-model="notes" :maxlength="6000" :rows="3" :disabled="disabled" />
    <BaseButton v-if="unboundQuestions.some((question) => !question.code && !question.filePath)" variant="tonal" :disabled="disabled" @click="emit('openSettings', unboundQuestions.find((question) => !question.code && !question.filePath))">
      {{ $t('analysis.automation.openSettings') }}
    </BaseButton>
    <p v-if="draftContext && (notes || Object.values(values).some(Boolean))" class="question__hint" role="status">
      {{ $t(draftSaved ? 'analysis.automation.draftSaved' : 'analysis.automation.draftUnavailable') }}
    </p>
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
  gap: 12px;
}
.question__item {
  min-width: 0;
  padding: 12px 0;
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
