import { describe, expect, it } from 'vitest'
import type { Dependency } from '@/entities/project/model/types'
import { dependencyRequirement, dependencySummaries, needsPackageMapping } from './dependencyDisplay'

function dependency(overrides: Partial<Dependency> = {}): Dependency {
  return {
    id: 'dependency',
    module: 'numpy',
    category: 'external',
    package: 'numpy',
    requirement: 'numpy>=1.24,<3',
    source: 'declared',
    locations: [],
    ...overrides,
  }
}

describe('dependency review decisions', () => {
  it('keeps known packages and unknown versions out of the questions', () => {
    expect(needsPackageMapping(dependency())).toBe(false)
    expect(needsPackageMapping(dependency({ requirement: null }))).toBe(false)
    expect(dependencyRequirement(dependency({ requirement: null }))).toEqual({ name: 'numpy', spec: '' })
  })

  it('asks only for an included unknown package, with no guessed mapping', () => {
    const unknown = dependency({ category: 'unresolved', module: 'custom', package: null, requirement: null })
    expect(needsPackageMapping(unknown)).toBe(true)
    expect(dependencyRequirement(unknown)).toEqual({ name: '', spec: '' })
    expect(needsPackageMapping(unknown, 'confirmed-package')).toBe(false)
    expect(needsPackageMapping(unknown, undefined, true)).toBe(false)
    expect(needsPackageMapping({ ...unknown, category: 'local' })).toBe(false)
    expect(needsPackageMapping({ ...unknown, category: 'stdlib' })).toBe(false)
  })

  it('keeps declaration constraints but only carries the marker into a package override', () => {
    const declared = dependency({ requirement: 'numpy[extra]>=1.24; python_version >= "3.11"' })
    expect(dependencyRequirement(declared)).toEqual({
      name: 'numpy[extra]',
      spec: '>=1.24; python_version >= "3.11"',
    })
    expect(dependencyRequirement(declared, 'replacement')).toEqual({
      name: 'replacement',
      spec: '; python_version >= "3.11"',
    })
    expect(dependencyRequirement(declared, 'replacement', '2.1')).toEqual({
      name: 'replacement',
      spec: '==2.1; python_version >= "3.11"',
    })
    expect(dependencyRequirement(declared, 'replacement>=4; python_version < "3.11"')).toEqual({
      name: 'replacement',
      spec: '>=4; python_version < "3.11"',
    })
    expect(dependencyRequirement(dependency(), 'replacement')).toEqual({ name: 'replacement', spec: '' })
  })

  it('shows the final requirements exactly, including packages with no import candidate', () => {
    const requirements = ['numpy==2.1', 'declared-only[extra]>=4; python_version >= "3.11"']
    expect(
      dependencySummaries([dependency()], { numpy: 'replacement' }, { numpy: '99' }, requirements),
    ).toEqual([
      { id: 'requirement-0', name: 'numpy', spec: '==2.1' },
      { id: 'requirement-1', name: 'declared-only[extra]', spec: '>=4; python_version >= "3.11"' },
    ])
    expect(dependencySummaries([dependency()], {}, {}, [])).toEqual([])
    expect(dependencySummaries([dependency()], { numpy: 'replacement' }, {})).toEqual([
      { id: 'dependency', name: 'replacement', spec: '' },
    ])
  })

  it('uses final unresolved modules for removed declarations and invalid mappings', () => {
    expect(needsPackageMapping(dependency(), undefined, false, ['numpy'])).toBe(true)
    expect(needsPackageMapping(dependency(), 'invalid mapping!', false, ['numpy'])).toBe(true)
    expect(needsPackageMapping(dependency(), 'invalid mapping!', true, ['numpy'])).toBe(false)
    expect(
      dependencySummaries([dependency()], { numpy: 'invalid mapping!' }, {}, undefined, ['numpy']),
    ).toEqual([])

    const unknown = dependency({ category: 'unresolved', package: null, requirement: null })
    expect(needsPackageMapping(unknown, undefined, false, [])).toBe(true)
  })
})
