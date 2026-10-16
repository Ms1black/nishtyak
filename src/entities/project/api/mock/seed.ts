import type { Dependency, Finding, Project, ProjectAnalysis, ProjectFile } from '../../model/types'

function at(daysAgo: number, hours: number, minutes: number): string {
  const date = new Date()
  date.setDate(date.getDate() - daysAgo)
  date.setHours(hours, minutes, 0, 0)
  return date.toISOString()
}

export function createSeedProjects(): Project[] {
  return [
    {
      id: 'p-sales-report',
      name: 'sales-report',
      sourceType: 'zip',
      sizeBytes: 248 * 1024,
      filesCount: 8,
      analysis: 'partial',
      archive: { status: 'not_built' },
      updatedAt: at(0, 12, 42),
    },
    {
      id: 'p-telegram-bot',
      name: 'telegram-bot',
      sourceType: 'zip',
      sizeBytes: 512 * 1024,
      filesCount: 18,
      analysis: 'full',
      archive: { status: 'ready', sizeBytes: 126 * 1024 },
      updatedAt: at(0, 11, 18),
    },
    {
      id: 'p-data-pipeline',
      name: 'data-pipeline',
      sourceType: 'zip',
      sizeBytes: 1.8 * 1024 * 1024,
      filesCount: 32,
      analysis: 'analyzing',
      archive: { status: 'unavailable' },
      updatedAt: at(0, 10, 56),
    },
    {
      id: 'p-image-resizer',
      name: 'image-resizer',
      sourceType: 'py',
      sizeBytes: 12 * 1024,
      filesCount: 1,
      analysis: 'full',
      archive: { status: 'ready', sizeBytes: 8 * 1024 },
      updatedAt: at(1, 17, 30),
    },
    {
      id: 'p-weather-api',
      name: 'weather-api',
      sourceType: 'zip',
      sizeBytes: 186 * 1024,
      filesCount: 12,
      analysis: 'full',
      archive: { status: 'not_built' },
      updatedAt: at(1, 15, 4),
    },
    {
      id: 'p-csv-cleaner',
      name: 'csv-cleaner',
      sourceType: 'py',
      sizeBytes: 24 * 1024,
      filesCount: 1,
      analysis: 'full',
      archive: { status: 'ready', sizeBytes: 11 * 1024 },
      updatedAt: at(2, 16, 20),
    },
  ]
}

const dep = (
  module: string,
  category: Dependency['category'],
  locations: [string, number][],
  extra: Partial<Dependency> = {},
): Dependency => ({
  id: `dep-${module}`,
  module,
  category,
  package: null,
  requirement: null,
  source: category === 'unresolved' ? 'unresolved' : 'inferred',
  locations: locations.map(([path, line]) => ({ path, line })),
  ...extra,
})

const finding = (code: string, severity: Finding['severity'], message: string, extra: Partial<Finding> = {}): Finding => ({
  id: `${code}:${extra.path ?? extra.module ?? ''}`,
  code,
  severity,
  message,
  ...extra,
})

const PROJECT_FILES: ProjectFile[] = [
  { path: 'pyproject.toml', size: 412 },
  { path: 'main.py', size: 284 },
  { path: 'src/__init__.py', size: 0 },
  { path: 'src/loader.py', size: 618 },
  { path: 'src/report.py', size: 951 },
  { path: 'data/sales.csv', size: 18_204 },
]

const SECRET_FILES: ProjectFile[] = [
  { path: '.env', size: 96, secret: 'api_token' },
  { path: 'credentials.json', size: 2_318, secret: 'private_key' },
]

function zipAnalysis(project: Project, partial: boolean): ProjectAnalysis {
  return {
    analysisId: `a-${project.id}`,
    projectId: project.id,
    status: partial ? 'partial' : 'full',
    files: partial ? [...PROJECT_FILES, ...SECRET_FILES] : PROJECT_FILES,
    dependencies: [
      dep('pandas', 'external', [['main.py', 1], ['src/loader.py', 1]], {
        package: 'pandas',
        requirement: 'pandas>=2.0',
        source: 'declared',
        declaredIn: 'pyproject.toml',
      }),
      dep('dotenv', 'external', [['src/loader.py', 3]], { package: 'python-dotenv' }),
      partial
        ? dep('slugify', 'unresolved', [['main.py', 3]])
        : dep('slugify', 'external', [['main.py', 3]], { package: 'python-slugify', requirement: 'python-slugify==8.0.4', source: 'author_environment' }),
      dep('openpyxl', 'external', [['src/report.py', 2]], {
        package: 'openpyxl',
        requirement: 'openpyxl==3.1.5',
        source: 'declared',
        declaredIn: 'pyproject.toml',
      }),
      dep('src', 'local', [['main.py', 2]]),
      dep('json', 'stdlib', [['src/report.py', 1]]),
      dep('os', 'stdlib', [['src/loader.py', 2]]),
      dep('pathlib', 'stdlib', [['src/loader.py', 4]]),
    ],
    entrypoints: ['main.py'],
    pythonRequires: '>=3.10',
    findings: [
      ...(partial
        ? [finding('DYNAMIC_IMPORT', 'warning', 'Dynamic import detected.', { path: 'src/loader.py', line: 14 })]
        : []),
      finding('DATA_DEPENDENCIES_UNVERIFIED', 'warning', 'Input files need author confirmation.'),
      finding('README_MISSING', 'info', 'No README was found at the project root.'),
      finding('LICENSE_MISSING', 'info', 'No license document was found at the project root.'),
    ],
  }
}

function scriptAnalysis(project: Project, fileName: string): ProjectAnalysis {
  return {
    analysisId: `a-${project.id}`,
    projectId: project.id,
    status: 'full',
    files: [{ path: fileName, size: project.sizeBytes }],
    dependencies: [
      dep('PIL', 'external', [[fileName, 2]], { package: 'pillow' }),
      dep('click', 'external', [[fileName, 3]], { package: 'click', requirement: 'click==8.1.7', source: 'author_environment' }),
      dep('pathlib', 'stdlib', [[fileName, 1]]),
    ],
    entrypoints: [fileName],
    pythonRequires: null,
    findings: [finding('README_MISSING', 'info', 'No README was found at the project root.')],
  }
}

export function createAnalysis(project: Project, fileName?: string): ProjectAnalysis {
  if (project.analysis === 'analyzing') {
    return {
      analysisId: `a-${project.id}`,
      projectId: project.id,
      status: 'analyzing',
      files: [],
      dependencies: [],
      entrypoints: [],
      pythonRequires: null,
      findings: [],
    }
  }
  if (project.sourceType === 'py') {
    return scriptAnalysis(project, fileName ?? `${project.name.replace(/-/g, '_')}.py`)
  }
  return zipAnalysis(project, project.analysis === 'partial')
}

export const FILE_CONTENTS: Record<string, string> = {
  'main.py': `import pandas as pd
from src.loader import load_sales
from slugify import slugify
df = load_sales('data/sales.csv')
report_name = f'report_{slugify(df.attrs["title"])}.json'
df.to_json(report_name, orient='records')`,
  'src/loader.py': `import pandas as pd
import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

def load_sales(path: str) -> pd.DataFrame:
    df = pd.read_csv(Path(path))
    df.attrs["title"] = os.getenv("REPORT_TITLE", "sales")
    return df

def load_plugin(name: str):
    return __import__(f"plugins.{name}")`,
  'src/report.py': `import json
from openpyxl import Workbook

def to_excel(rows: list[dict], path: str) -> None:
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(list(row.values()))
    wb.save(path)`,
  'src/__init__.py': '',
  'pyproject.toml': `[project]
name = "sales-report"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
  "pandas>=2.0",
  "openpyxl==3.1.5",
]`,
  'data/sales.csv': `date,region,amount
2026-09-01,north,1200
2026-09-01,south,860
2026-09-02,north,1430
2026-09-02,south,910`,
}
