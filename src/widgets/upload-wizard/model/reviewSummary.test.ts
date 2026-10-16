import { describe, expect, it } from 'vitest'
import type { Finding } from '@/entities/project/model/types'
import { reviewNotices } from './reviewSummary'

function finding(code: string, severity: Finding['severity'] = 'warning'): Finding {
  return { id: code, code, severity, message: code }
}

describe('review summary', () => {
  it('groups repeated environment and data uncertainty instead of multiplying tasks', () => {
    expect(
      reviewNotices([
        finding('DATA_NOT_CONFIRMED'),
        finding('INPUT_PATH_UNVERIFIED'),
        finding('DATA_DEPENDENCIES_UNVERIFIED'),
        finding('VERSION_NOT_PINNED'),
        finding('VERSION_NOT_PINNED'),
        finding('PYTHON_VERSION_UNKNOWN'),
        finding('README_MISSING'),
        finding('LICENSE_MISSING'),
      ]),
    ).toEqual(['data', 'environment'])
  })

  it('keeps errors and package questions in their actionable controls, and ignores informational inference', () => {
    expect(
      reviewNotices([
        finding('LOCAL_MODULE_EXCLUDED', 'error'),
        finding('DEPENDENCY_UNRESOLVED'),
        finding('RUN_INFERRED', 'info'),
        finding('DATA_FILES_SUMMARY', 'info'),
        finding('DYNAMIC_IMPORT'),
        finding('RUN_TARGET_AMBIGUOUS'),
      ]),
    ).toEqual(['launch', 'other'])
  })
})
