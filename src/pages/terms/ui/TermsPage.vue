<script setup lang="ts">
import { computed } from 'vue'
import { locale } from '@/shared/i18n'
import { BaseCard } from '@/shared/ui'
import { termsEn } from '../content/en'
import { termsRu } from '../content/ru'

const terms = computed(() => (locale.value === 'en' ? termsEn : termsRu))
</script>

<template>
  <div class="container terms">
    <nav class="terms__toc" :aria-label="terms.tocLabel">
      <p class="terms__toc-title">{{ terms.tocLabel }}</p>
      <ol>
        <li v-for="section in terms.sections" :key="section.id">
          <a :href="`#terms-${section.id}`">{{ section.title }}</a>
        </li>
      </ol>
    </nav>

    <BaseCard tag="article" padding="lg" class="terms__body">
      <header class="terms__head">
        <h1 class="terms__title">{{ terms.title }}</h1>
        <p class="terms__updated">{{ terms.updated }}</p>
        <p class="terms__intro">{{ terms.intro }}</p>
      </header>

      <section v-for="section in terms.sections" :id="`terms-${section.id}`" :key="section.id" class="terms__section">
        <h2 class="terms__section-title">{{ section.title }}</h2>
        <p v-for="(text, index) in section.paragraphs" :key="index" class="terms__text">{{ text }}</p>
        <ul v-if="section.items" class="terms__list">
          <li v-for="(item, index) in section.items" :key="index">{{ item }}</li>
        </ul>
      </section>
    </BaseCard>
  </div>
</template>

<style scoped>
.terms {
  display: grid;
  grid-template-columns: 260px minmax(0, 820px);
  justify-content: center;
  gap: 48px;
  align-items: start;
}

.terms__toc {
  position: sticky;
  top: 24px;
  padding-top: 32px;
}

.terms__toc-title {
  margin-bottom: 14px;
  color: var(--color-text-muted);
  font-size: 12px;
}

.terms__toc ol {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.terms__toc a {
  color: var(--color-text-secondary);
  font-size: 14px;
  line-height: 1.4;
  transition: color var(--transition);
}

.terms__toc a:hover {
  color: var(--color-text);
}

.terms__title {
  font-family: var(--font-display);
  font-size: 32px;
  font-weight: 500;
  line-height: 1.2;
}

.terms__updated {
  margin-top: 8px;
  color: var(--color-text-muted);
  font-size: 13px;
}

.terms__intro {
  margin-top: 24px;
  color: var(--color-text-secondary);
  font-size: 16px;
  line-height: 1.6;
}

.terms__section {
  margin-top: 36px;
  padding-top: 32px;
  border-top: 1px solid var(--color-border);
  scroll-margin-top: 24px;
}

.terms__section-title {
  font-family: var(--font-display);
  font-size: 20px;
  font-weight: 500;
}

.terms__text {
  margin-top: 14px;
  color: var(--color-text-secondary);
  font-size: 15px;
  line-height: 1.65;
}

.terms__list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 14px;
}

.terms__list li {
  position: relative;
  padding-left: 20px;
  color: var(--color-text-secondary);
  font-size: 15px;
  line-height: 1.65;
}

.terms__list li::before {
  content: '';
  position: absolute;
  top: 11px;
  left: 4px;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--color-text-subtle);
}

@media (max-width: 1024px) {
  .terms {
    grid-template-columns: 1fr;
  }

  .terms__toc {
    display: none;
  }
}

@media (max-width: 640px) {
  .terms__title {
    font-size: 26px;
  }
}
</style>
