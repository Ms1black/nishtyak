import { reactive, watch } from 'vue'
import { projectApi, type VerificationDocument, type VerificationStatus } from '@/entities/project'
import type { ProjectApi } from '@/entities/project/api'
import { ApiError } from '@/shared/api'

export interface VerificationForm {
  status: VerificationStatus
  pythonVersion: string
  platform: string
  command: string
  output: string
  notes: string
}

function emptyForm(): VerificationForm {
  return { status: 'not_checked', pythonVersion: '', platform: '', command: '', output: '', notes: '' }
}

export function readmeCommands(content: string): { title: string; content: string }[] {
  const commands: { title: string; content: string }[] = []
  const seen = new Set<string>()
  const pattern = /^```(?:bash|sh|shell|zsh|console|powershell|pwsh|cmd|bat)\s*\r?\n([\s\S]*?)^```\s*$/gm
  for (const match of content.matchAll(pattern)) {
    const value = match[1]?.trim()
    if (!value || seen.has(value)) continue
    seen.add(value)
    const heading = content.slice(0, match.index).match(/^#{1,6}\s+(.+)$/gm)?.at(-1)?.replace(/^#+\s+/, '') ?? ''
    commands.push({ title: heading, content: value })
  }
  return commands
}

type ArchiveApi = Pick<ProjectApi, 'getReadme' | 'getVerification' | 'saveVerification'>

export class ArchiveReview {
  readonly state = reactive({
    readme: '',
    readmeLoading: false,
    readmeError: null as string | null,
    verification: null as VerificationDocument | null,
    verificationLoading: false,
    verificationError: null as string | null,
    verificationConflict: false,
    saving: false,
    saved: false,
    form: emptyForm(),
  })
  private epoch = 0
  private projectId = ''
  private archiveTag: string | null = null

  constructor(private readonly api: ArchiveApi = projectApi) {
    watch(() => this.state.form, () => { this.state.saved = false }, { deep: true, flush: 'sync' })
  }

  async open(projectId: string, archiveTag?: string) {
    this.reset()
    this.projectId = projectId
    this.archiveTag = archiveTag ?? null
    await Promise.allSettled([this.loadReadme(), this.loadVerification()])
  }

  reset() {
    this.epoch += 1
    this.projectId = ''
    this.archiveTag = null
    Object.assign(this.state, {
      readme: '', readmeLoading: false, readmeError: null,
      verification: null, verificationLoading: false, verificationError: null,
      verificationConflict: false, saving: false, saved: false, form: emptyForm(),
    })
  }

  async loadReadme() {
    if (!this.projectId) return
    const epoch = this.epoch
    this.state.readmeLoading = true
    this.state.readmeError = null
    try {
      const result = await this.api.getReadme(this.projectId)
      if (epoch === this.epoch) this.state.readme = result.content
    } catch {
      if (epoch === this.epoch) this.state.readmeError = 'ready.readme.error'
    } finally {
      if (epoch === this.epoch) this.state.readmeLoading = false
    }
  }

  async loadVerification() {
    if (!this.projectId || this.state.saving) return
    const epoch = this.epoch
    this.state.verificationLoading = true
    this.state.verificationError = null
    this.state.verificationConflict = false
    try {
      const result = await this.api.getVerification(this.projectId)
      if (epoch !== this.epoch) return
      this.state.verification = result
      this.state.form = result.verification ? {
        status: result.verification.status,
        pythonVersion: result.verification.pythonVersion,
        platform: result.verification.platform,
        command: result.verification.command,
        output: result.verification.output,
        notes: result.verification.notes,
      } : emptyForm()
    } catch {
      if (epoch === this.epoch) this.state.verificationError = 'ready.verification.loadError'
    } finally {
      if (epoch === this.epoch) this.state.verificationLoading = false
    }
  }

  async save(canEdit: boolean) {
    const doc = this.state.verification
    const archiveTag = this.archiveTag ?? doc?.archiveTag
    if (!canEdit || !this.projectId || !doc?.analysisId || !archiveTag || this.state.saving || this.state.verificationLoading) return
    if (doc.archiveTag && doc.archiveTag !== archiveTag) {
      this.state.verificationError = 'ready.verification.conflict'
      this.state.verificationConflict = true
      return
    }
    const epoch = this.epoch
    this.state.saving = true
    this.state.saved = false
    this.state.verificationError = null
    this.state.verificationConflict = false
    try {
      const result = await this.api.saveVerification(this.projectId, {
        expectedRevision: doc.revision,
        analysisId: doc.analysisId,
        archiveTag,
        ...this.state.form,
      })
      if (epoch !== this.epoch) return
      this.state.verification = result
      if (result.verification) {
        const { status, pythonVersion, platform, command, output, notes } = result.verification
        this.state.form = { status, pythonVersion, platform, command, output, notes }
      }
      this.state.saved = true
    } catch (cause) {
      if (epoch !== this.epoch) return
      this.state.verificationConflict = cause instanceof ApiError && cause.status === 409
      this.state.verificationError = this.state.verificationConflict
        ? 'ready.verification.conflict' : 'ready.verification.saveError'
    } finally {
      if (epoch === this.epoch) this.state.saving = false
    }
  }
}
