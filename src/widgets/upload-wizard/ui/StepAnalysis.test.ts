// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { createApp, createSSRApp, h, nextTick, reactive } from 'vue'
import { renderToString } from '@vue/server-renderer'
import StepAnalysis from './StepAnalysis.vue'

const { state } = vi.hoisted(() => ({ state: { wizard: {} as Record<string, unknown> } }))
vi.mock('../model/uploadWizardStore', () => ({ useUploadWizardStore: () => state.wizard }))
vi.mock('@/shared/i18n', () => ({ t: (key: string) => key, intlLocale: { value: 'ru' } }))
vi.mock('./ProjectToolsPanel.vue', () => ({ default: { render: () => h('div', { 'data-project-tools': '' }) } }))
vi.mock('./ReadinessPanel.vue', () => ({ default: { render: () => h('aside', { 'data-readiness': '' }) } }))

function readyState() {
  return {
    project: { id: 'project', name: 'Experiment' },
    analysis: {
      analysisId: 'analysis', status: 'full',
      files: [{ path: 'experiment.py', size: 100 }, { path: 'measurements.csv', size: 40 }],
      dependencies: [{ id: 'numpy', module: 'numpy', category: 'external', package: 'numpy', requirement: 'numpy>=1.26', source: 'declared', locations: [] }],
      findings: [], entrypoints: ['experiment.py'], pythonRequires: null,
      launchCandidates: [], optionalGroups: [],
    },
    decisions: {
      analysisId: 'analysis', packages: {}, versions: {}, excludedModules: [], excludedFiles: [],
      runSpec: null, pythonVersion: null, dataConfirmed: true,
      inputDescription: 'Existing input notes', expectedOutput: 'Existing result notes', description: 'Existing author summary',
      selectedExtras: [], declarationActions: {},
    },
    plan: {
      state: 'ready_to_verify', requirements: ['numpy>=1.26'], findings: [],
      runSpec: { kind: 'script', target: 'experiment.py', args: [], workingDirectory: '.' },
      description: 'Automatically prepared project summary', descriptionOrigin: 'inferred',
      automaticResolution: { status: 'ready', packages: ['numpy==2.2.6'], target: { pythonVersion: '3.11', platform: 'linux-x86_64' } },
    },
    automation: { phase: 'ready' }, advancedOpen: false,
    isLoading: false, isAnalyzing: false, isBusy: false, isAutomating: false, canEdit: true,
    saveState: 'saved', error: null,
    update: vi.fn(), setRunSpec: vi.fn(), setPackage: vi.fn(), setVersion: vi.fn(), toggleModule: vi.fn(),
    answerQuestions: vi.fn(), replaceSource: vi.fn(),
  }
}

async function render() {
  const app = createSSRApp({ render: () => h(StepAnalysis) })
  app.config.globalProperties.$t = (key: string) => key
  const root = document.createElement('div')
  root.innerHTML = await renderToString(app)
  return root
}

describe('analysis recovery', () => {
  it('shows a retriable plan failure instead of an empty successful analysis summary', async () => {
    state.wizard = {
      project: { id: 'project', name: 'Experiment' },
      analysis: { analysisId: 'analysis', status: 'full', files: [], findings: [] },
      decisions: { excludedFiles: [] },
      plan: null, error: 'Plan temporarily unavailable', saveState: 'idle',
      isLoading: false, isAnalyzing: false,
    }
    const app = createSSRApp({ render: () => h(StepAnalysis) })
    app.config.globalProperties.$t = (key: string) => key
    const html = await renderToString(app)
    expect(html).toContain('Plan temporarily unavailable')
    expect(html).toContain('common.retry')
    expect(html).not.toContain('analysis.review.title')
    expect(html).not.toContain('analysis.deps.empty')
  })

  it('keeps the normal result readonly and puts manual dependency controls under expert settings', async () => {
    const wizard = readyState()
    state.wizard = wizard
    const saved = JSON.stringify(wizard.decisions)
    const root = await render()
    expect(root.querySelector('.overview__requirements')?.textContent).toContain('numpy==2.2.6')
    expect(root.querySelector('#section-data')?.textContent).toContain('measurements.csv')
    expect(root.querySelector('#section-data textarea, input[type="checkbox"]')).toBeNull()
    const expert = root.querySelector<HTMLDetailsElement>('.analysis__advanced')!
    expect(expert.open).toBe(false)
    expect(expert.querySelector('summary')?.textContent).toContain('analysis.review.expertTitle')
    expect(root.querySelector('#section-dependencies')?.closest('details.analysis__advanced')).toBe(expert)
    for (const control of root.querySelectorAll('input, select, textarea')) {
      expect(control.closest('details.analysis__advanced')).toBe(expert)
    }
    expect(JSON.stringify(wizard.decisions)).toBe(saved)
    expect(wizard.update).not.toHaveBeenCalled()
  })

  it('shows a single automation batch for disputed imports and constraints without duplicate mapping forms', async () => {
    const wizard = readyState()
    state.wizard = {
      ...wizard,
      automation: {
        phase: 'needs_input', questionnaireId: 'a'.repeat(20),
        questions: [
          { id: 'package', aspect: 'dependencies', kind: 'package_mapping', module: 'lab_unknown' },
          { id: 'constraint', aspect: 'environment', kind: 'version_constraint', module: 'numpy' },
        ],
      },
    }
    const root = await render()
    expect(root.querySelectorAll('form.question')).toHaveLength(1)
    expect(root.querySelectorAll('.question__item')).toHaveLength(2)
    expect(root.textContent).toContain('analysis.automation.batchTitle')
    expect(root.querySelectorAll('.questions')).toHaveLength(1)
    expect(root.querySelector('.mapping')).toBeNull()
    expect(root.textContent).not.toContain('analysis.deps.unresolvedHint')
  })

  it('lists only retained data candidates and does not treat project documentation as data', async () => {
    const wizard = readyState()
    state.wizard = {
      ...wizard,
      analysis: { ...wizard.analysis, files: [...wizard.analysis.files,
        { path: 'private.csv', size: 12 }, { path: 'README.txt', size: 12 }, { path: 'requirements.txt', size: 12 },
      ] },
      plan: { ...wizard.plan, files: ['experiment.py', 'measurements.csv', 'README.txt', 'requirements.txt'] },
    }
    const root = await render()
    const data = root.querySelector('#section-data')!
    expect(data.textContent).toContain('measurements.csv')
    for (const path of ['private.csv', 'README.txt', 'requirements.txt']) expect(data.textContent).not.toContain(path)
  })

  it('keeps concrete missing inputs visible in normal facts and preserves secret blockers', async () => {
    const wizard = readyState()
    state.wizard = {
      ...wizard,
      plan: {
        ...wizard.plan,
        state: 'blocked',
        findings: [
          { id: 'missing', code: 'INPUT_PATH_UNVERIFIED', severity: 'warning', path: 'experiment.py', message: 'Не подтверждён входной путь "missing-measurements.csv".' },
          { id: 'secret', code: 'SECRET_DETECTED', severity: 'error', path: 'config.py', message: 'Credential detected in config.py' },
        ],
      },
      automation: { phase: 'blocked' },
    }
    const root = await render()
    const data = root.querySelector('#section-data')!
    expect(data.closest('.analysis__advanced')).toBeNull()
    expect(data.textContent).toContain('missing-measurements.csv')
    expect(data.textContent).toContain('analysis.data.replaceZip')
    expect(root.querySelector('.questions[role="alert"]')?.textContent).toContain('Credential detected in config.py')
    expect(root.textContent).toContain('analysis.automatic.fix')
  })

  it('opens ZIP replacement directly from a missing input and opens author additions without expert settings', async () => {
    const wizard = readyState()
    state.wizard = {
      ...wizard,
      plan: {
        ...wizard.plan,
        findings: [{ id: 'missing', code: 'INPUT_PATH_UNVERIFIED', severity: 'warning', path: 'experiment.py', message: 'Не подтверждён входной путь "missing.csv".' }],
      },
    }
    const app = createApp({ render: () => h(StepAnalysis) })
    app.config.globalProperties.$t = (key: string) => key
    const root = document.createElement('div')
    document.body.append(root)
    app.mount(root)
    try {
      const data = root.querySelector('#section-data')!
      Object.defineProperty(data, 'scrollIntoView', { value: vi.fn() })
      expect(data.querySelector('input[type="file"], textarea')).toBeNull()
      data.querySelector<HTMLButtonElement>('.data__missing button')!.click()
      await nextTick()
      expect(data.querySelector('input[type="file"][accept=".zip,application/zip"]')).not.toBeNull()
      root.querySelector<HTMLButtonElement>('.overview__description .overview__link')!.click()
      await nextTick()
      await nextTick()
      expect(data.querySelectorAll('textarea')).toHaveLength(3)
      expect(data.querySelector<HTMLTextAreaElement>('#data-description')!.value).toBe(wizard.decisions.description)
      expect(root.querySelector<HTMLDetailsElement>('.analysis__advanced')!.open).toBe(false)
      expect(wizard.update).not.toHaveBeenCalled()
      expect(wizard.decisions.dataConfirmed).toBe(true)
    } finally {
      app.unmount()
      root.remove()
    }
  })

  it('opens the launch editor directly from the summary with one click and leaves the detected command unchanged', async () => {
    const wizard = reactive(readyState())
    state.wizard = wizard
    const app = createApp({ render: () => h(StepAnalysis) })
    app.config.globalProperties.$t = (key: string) => key
    const root = document.createElement('div')
    document.body.append(root)
    app.mount(root)
    const section = root.querySelector<HTMLElement>('#section-run')!
    Object.defineProperty(section, 'scrollIntoView', { value: vi.fn() })
    try {
      expect(section.querySelector('textarea, .segmented')).toBeNull()
      const edit = [...root.querySelectorAll<HTMLButtonElement>('.overview button')]
        .find((button) => button.textContent?.trim() === 'analysis.review.editRun')!
      edit.click()
      await nextTick()
      await nextTick()
      expect(root.querySelector<HTMLDetailsElement>('.analysis__advanced')!.open).toBe(true)
      expect(section.querySelector('#run-args')).not.toBeNull()
      expect(section.querySelector('#run-cwd')).not.toBeNull()
      expect(section.textContent).toContain('analysis.run.done')
      expect(section.textContent).toContain('python experiment.py')
      expect(wizard.setRunSpec).not.toHaveBeenCalled()
      expect(wizard.update).not.toHaveBeenCalled()
    } finally {
      app.unmount()
      root.remove()
    }
  })
})
